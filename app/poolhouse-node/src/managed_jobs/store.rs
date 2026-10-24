//! Bounded private pending records; current directory binding is not original-root provenance.
use std::{fs::File, io::{Read,Write}};
use serde::{Deserialize,Serialize};
use super::{Failure,Record};
const LIMIT:usize=crate::wire::MAX_FRAME;
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Envelope { version:u32, operation:String, records:Vec<Record>, checksum:String }
fn bytes(records:&[Record])->Result<Vec<u8>,Failure> {
    let payload=serde_json::to_vec(records).map_err(|_|Failure::Invalid)?;
    let value=Envelope { version:1, operation:"managed_test_unit_pending".into(),records:records.to_vec(),checksum:crate::fsutil::sha256_hex(&payload) };
    let bytes=serde_json::to_vec(&value).map_err(|_|Failure::Invalid)?;
    // Reuse the local frame bound for this entire private record table, not a new budget.
    if bytes.len()>LIMIT { return Err(Failure::Invalid); } Ok(bytes)
}
fn parse(bytes:&[u8])->Result<Vec<Record>,Failure> {
    let value:Envelope=serde_json::from_slice(bytes).map_err(|_|Failure::Invalid)?;
    if value.version!=1 || value.operation!="managed_test_unit_pending" ||
        value.checksum!=crate::fsutil::sha256_hex(&serde_json::to_vec(&value.records).map_err(|_|Failure::Invalid)?) {
        return Err(Failure::Invalid);
    } Ok(value.records)
}
#[cfg(unix)]
pub(super) struct Store {
    dir:File,
    #[cfg(test)] pub fail_at:Option<&'static str>,
}
#[cfg(unix)]
impl Store {
    pub fn open(parent:File)->Result<Self,Failure> {
        use std::os::fd::AsRawFd;
        // SAFETY: fixed NUL-terminated leaf and live parent descriptor.
        let created=unsafe { libc::mkdirat(parent.as_raw_fd(), c"managed-pending".as_ptr(),0o700) } ==0;
        if !created {
            let e=std::io::Error::last_os_error();
            if e.kind()!=std::io::ErrorKind::AlreadyExists { return Err(Failure::Io(e.kind())); }
        }
        let dir=open_at(&parent,c"managed-pending",libc::O_RDONLY|libc::O_DIRECTORY,0)?;
        private(&dir,true)?;
        parent.sync_all().map_err(io)?;
        let store=Self {dir, #[cfg(test)] fail_at:None};
        if created { store.write_inner(&[],true)?; }
        Ok(store)
    }
    pub fn read(&self)->Result<Vec<Record>,Failure> {
        let file=match open_at(&self.dir,c"records.json",libc::O_RDONLY,0) {
            Ok(f)=>f,
            Err(Failure::Io(std::io::ErrorKind::NotFound))=>return Err(Failure::Invalid),
            Err(e)=>return Err(e),
        };
        private(&file,false)?;
        if file.metadata().map_err(io)?.len()>LIMIT as u64 { return Err(Failure::Invalid); }
        let mut data=Vec::new(); file.take((LIMIT+1) as u64).read_to_end(&mut data).map_err(io)?;
        if data.len()>LIMIT { return Err(Failure::Invalid); }
        parse(&data)
    }
    pub fn write(&self,records:&[Record])->Result<(),Failure> { self.write_inner(records,false) }
    fn write_inner(&self,records:&[Record],initialize:bool)->Result<(),Failure> {
        use std::{ffi::CString,os::fd::AsRawFd};
        let data=bytes(records)?;
        let leaf=CString::new(format!("tmp-{}",crate::fsutil::random_hex(16).map_err(|_|Failure::Invalid)?)).map_err(|_|Failure::Invalid)?;
        let mut file=open_at(&self.dir,&leaf,libc::O_WRONLY|libc::O_CREAT|libc::O_EXCL,0o600)?;
        let before=(|| {
            private(&file,false)?;
            file.write_all(&data).map_err(io)?; file.sync_all().map_err(io)?;
            #[cfg(test)] if self.fail_at==Some("before_rename") { return Err(Failure::Io(std::io::ErrorKind::Other)); }
            Ok(())
        })();
        if let Err(e)=before {
            // SAFETY: only our successfully exclusively created fixed-parent temporary leaf.
            unsafe { libc::unlinkat(self.dir.as_raw_fd(),leaf.as_ptr(),0); } return Err(e);
        }
        // Initialization publishes without replacing any existing entry. A crash before
        // removing its temporary hardlink leaves a detectable invalid link count.
        // SAFETY: all names use the retained private directory; no caller path is used.
        let published=unsafe {
            if initialize { libc::linkat(self.dir.as_raw_fd(),leaf.as_ptr(),self.dir.as_raw_fd(),c"records.json".as_ptr(),0) }
            else { libc::renameat(self.dir.as_raw_fd(),leaf.as_ptr(),self.dir.as_raw_fd(),c"records.json".as_ptr()) }
        };
        if published!=0 {
            // Publication outcome is conservatively unknown; preserve both alternatives.
            return Err(Failure::PersistenceUnknown);
        }
        if initialize {
            // SAFETY: remove only this invocation's exclusive temporary entry.
            if unsafe {libc::unlinkat(self.dir.as_raw_fd(),leaf.as_ptr(),0)}!=0 { return Err(Failure::PersistenceUnknown); }
        }
        #[cfg(test)] if self.fail_at==Some("after_rename") { return Err(Failure::PersistenceUnknown); }
        self.dir.sync_all().map_err(|_|Failure::PersistenceUnknown)
    }
}
#[cfg(unix)]
fn open_at(dir:&File,name:&std::ffi::CStr,flags:i32,mode:libc::mode_t)->Result<File,Failure> {
    use std::os::fd::{AsRawFd,FromRawFd};
    // SAFETY: live directory and NUL-terminated fixed/generated leaf; ownership transfers once.
    let fd=unsafe { libc::openat(dir.as_raw_fd(),name.as_ptr(),flags|libc::O_NOFOLLOW|libc::O_CLOEXEC|libc::O_NONBLOCK,mode as libc::c_uint) };
    if fd<0 { Err(io(std::io::Error::last_os_error())) } else { Ok(unsafe { File::from_raw_fd(fd) }) }
}
#[cfg(unix)]
fn private(file:&File,directory:bool)->Result<(),Failure> {
    use std::os::unix::fs::MetadataExt;
    let m=file.metadata().map_err(io)?;
    // SAFETY: geteuid has no preconditions.
    if m.uid()!=unsafe{libc::geteuid()} || m.mode()&0o077!=0 ||
        (directory && !m.is_dir()) || (!directory && (!m.is_file() || m.nlink()!=1)) { return Err(Failure::Invalid); } Ok(())
}
fn io(e:std::io::Error)->Failure { Failure::Io(e.kind()) }
#[cfg(not(unix))]
pub(super) struct Store;
#[cfg(not(unix))]
impl Store {
    pub fn open(_:File)->Result<Self,Failure> { Err(Failure::Io(std::io::ErrorKind::Unsupported)) }
    pub fn read(&self)->Result<Vec<Record>,Failure> { Err(Failure::Io(std::io::ErrorKind::Unsupported)) }
    pub fn write(&self,_:&[Record])->Result<(),Failure> { Err(Failure::Io(std::io::ErrorKind::Unsupported)) }
}
