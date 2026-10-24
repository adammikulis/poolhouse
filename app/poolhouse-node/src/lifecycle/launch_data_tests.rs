//! Untrusted DATA fixtures only. No NativeGraph implementation, native reservation,
//! executable, model or successful OS launch is manufactured here.
use super::*;
struct Data<'a> { args: &'a [&'a [u8]], env: &'a [(&'a [u8], &'a [u8])], limits: Limits,
    profile: LaunchAccountingProfile, mismatch: u8, poison_limits: bool }
impl View for Data<'_> {
    fn binding(&self) -> BindingData<'_> {
        let mut value = BindingData { expected_target: LaunchTargetKind::Worker, target: LaunchTargetKind::Worker,
            reserved_target: LaunchTargetKind::Worker, expected_digest: &[1;32], digest: &[1;32], reserved_digest: &[1;32],
            inputs: &[2;32], reserved_inputs: &[2;32], generation: "generation", reserved_generation: "generation",
            retained_generation: "generation", installation: ("installation","generation"), expected_installation: ("installation","generation") };
        match self.mismatch {
            1 => value.expected_target=LaunchTargetKind::Producer, 2 => value.reserved_target=LaunchTargetKind::Producer,
            3 => value.expected_digest=&[3;32], 4 => value.reserved_digest=&[3;32], 5 => value.reserved_inputs=&[3;32],
            6 => value.reserved_generation="other", 7 => value.retained_generation="other",
            8 => value.expected_installation=("other","generation"), _ => (),
        }
        value
    }
    fn profile(&self) -> LaunchAccountingProfile { self.profile }
    fn limits(&self) -> Limits { assert!(!self.poison_limits,"limits read before exact plan binding"); self.limits }
    fn argument_count(&self) -> usize { self.args.len() }
    fn environment_count(&self) -> usize { self.env.len() }
    fn argument(&self,index:usize)->Option<&[u8]>{ self.args.get(index).copied() }
    fn environment(&self,index:usize)->Option<(&[u8],&[u8])>{ self.env.get(index).copied() }
}
fn limits()->Limits{ Limits {os_bytes:1_000,reserved_bytes:1_000,string_bytes:1_000,arguments:10,environment:10,
    vector_bytes:1_000,overhead:0,kernel_pointer:8,vector_pointer:8,filename:0,work:100_000} }
fn data<'a>(args:&'a[&'a[u8]],env:&'a[(&'a[u8],&'a[u8])])->Data<'a>{Data{args,env,limits:limits(),profile:LaunchAccountingProfile::MacNativeImage,mismatch:0,poison_limits:false}}
#[test]
fn exact_target_plan_digest_snapshot_generation_and_installation_precede_budget() {
    for mismatch in 1..=8 {
        let mut value=data(&[b""],&[]);value.mismatch=mismatch;value.poison_limits=true;
        assert_eq!(validate(&value),Err(LaunchDataError::Binding));
    }
}
#[test]
fn mac_and_linux_account_different_native_layouts_and_keep_exact_values() {
    let args:[&[u8];2]=[b"",b"a\xff"];
    let env:[(&[u8],&[u8]);2]=[(b"K",b"v"),(b"k",b"")];
    let mut value=data(&args,&env);
    assert_eq!(validate(&value),Ok(LaunchAccounting{arguments:2,environment:2,string_bytes:11,kernel_bytes:64,vector_bytes:48,allocation_bytes:59}));
    value.profile=LaunchAccountingProfile::LinuxNativeImage;value.limits.filename=9;
    assert_eq!(validate(&value),Ok(LaunchAccounting{arguments:2,environment:2,string_bytes:11,kernel_bytes:52,vector_bytes:48,allocation_bytes:59}));
    assert_eq!(value.args,args);assert_eq!(value.env,env); // No sorting, Unicode conversion or inheritance.
}
#[test]
fn complete_argv_requires_an_explicit_first_value_but_that_value_may_be_empty() {
    assert_eq!(validate(&data(&[],&[])),Err(LaunchDataError::EmptyArgv));
    assert_eq!(validate(&data(&[b""],&[])),Ok(LaunchAccounting{arguments:1,environment:0,string_bytes:1,kernel_bytes:32,vector_bytes:24,allocation_bytes:25}));
}
#[test]
fn nul_delimiter_empty_and_duplicate_keys_refuse_without_restricting_unix_bytes() {
    for args in [[b"a\0b".as_slice()],[b"\0".as_slice()]] {assert_eq!(validate(&data(&args,&[])),Err(LaunchDataError::Null));}
    for name in [b"".as_slice(),b"A=B"] {assert_eq!(validate(&data(&[b"arg"],&[(name,b"v")])),Err(LaunchDataError::Key));}
    assert_eq!(validate(&data(&[b"arg"],&[(b"K",b"1"),(b"K",b"2")])),Err(LaunchDataError::DuplicateKey));
    assert_eq!(validate(&data(&[b"arg"],&[(b"K",b"v\0")])),Err(LaunchDataError::Null));
    assert!(validate(&data(&[b"\xff\n"],&[(b"\xff",b"=\r\n\xff"),(b" k",b"")])).is_ok());
}
#[test]
fn each_real_budget_dimension_is_checked_and_overflow_never_wraps() {
    for changed in 0..6 {
        let mut value=data(&[b""],&[(b"K",b"v")]);
        match changed {0=>value.limits.os_bytes=39,1=>value.limits.reserved_bytes=36,2=>value.limits.vector_bytes=31,
            3=>value.limits.string_bytes=3,4=>value.limits.arguments=0,_=>value.limits.environment=0}
        assert_eq!(validate(&value),Err(LaunchDataError::Limit));
    }
    let mut value=data(&[b""],&[]);value.profile=LaunchAccountingProfile::LinuxNativeImage;
    value.limits.filename=u64::MAX;value.limits.string_bytes=u64::MAX;value.limits.os_bytes=u64::MAX;value.limits.reserved_bytes=u64::MAX;
    assert_eq!(validate(&value),Err(LaunchDataError::Overflow));
    value=data(&[b""],&[]);value.limits.work=0;assert_eq!(validate(&value),Err(LaunchDataError::Work));
    value.limits.work=100;value.limits.kernel_pointer=0;assert_eq!(validate(&value),Err(LaunchDataError::Budget));
}
#[test]
fn windows_preserves_raw_units_but_cannot_complete_without_native_dependencies() {
    let args:[&[u8];1]=[&[0xd8,0x00]]; // Isolated UTF-16 surrogate retained as raw units, without Unicode replacement.
    let mut value=data(&args,&[]);value.profile=LaunchAccountingProfile::WindowsNativeUnavailable;
    assert_eq!(validate(&value),Err(LaunchDataError::WindowsNativeUnavailable));
    let drive:[(&[u8],&[u8]);1]=[(&[0,b'=',0,b'C',0,b':'],&[0,b'C',0,b':'])];
    value.env=&drive;assert_eq!(validate(&value),Err(LaunchDataError::WindowsNativeUnavailable));value.env=&[];
    let odd:[&[u8];1]=[&[1]];value.args=&odd;assert_eq!(validate(&value),Err(LaunchDataError::Encoding));
    let nul:[&[u8];1]=[&[0,0]];value.args=&nul;assert_eq!(validate(&value),Err(LaunchDataError::Null));
}
#[test]
fn compat_pointer_width_total_resource_budget_counts_strings_plus_actual_vectors() {
    let mut value=data(&[b""],&[]);
    value.limits.kernel_pointer=4;value.limits.vector_pointer=8;
    value.limits.os_bytes=16;value.limits.reserved_bytes=16;value.limits.vector_bytes=24;
    assert_eq!(validate(&value),Err(LaunchDataError::Limit)); // Actual owned input is 1+24 bytes, not kernel's 16.
    value.limits.reserved_bytes=25;
    assert_eq!(validate(&value),Ok(LaunchAccounting{arguments:1,environment:0,string_bytes:1,kernel_bytes:16,vector_bytes:24,allocation_bytes:25}));
    value.limits.overhead=1;assert_eq!(validate(&value),Err(LaunchDataError::Limit));
    value.limits.overhead=u64::MAX;assert_eq!(validate(&value),Err(LaunchDataError::Overflow));
    value.limits.overhead=0;value.limits.kernel_pointer=8;value.limits.vector_pointer=4;
    value.limits.os_bytes=32;value.limits.reserved_bytes=13;value.limits.vector_bytes=12;
    assert_eq!(validate(&value),Ok(LaunchAccounting{arguments:1,environment:0,string_bytes:1,kernel_bytes:32,vector_bytes:12,allocation_bytes:13}));
}
