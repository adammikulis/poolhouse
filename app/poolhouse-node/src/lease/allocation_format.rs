//! Private compatibility fence DATA. Older Table readers must reject the object
//! in `leases`; this is not native installation, original-root or rollback proof.
use serde::{Deserialize,Serialize};
use crate::contract::ResourceGeneration;
use super::{table::Table,types::Lease};
#[derive(Clone,Debug,PartialEq,Eq,Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Fence {pub authority:ResourceGeneration,pub generation:u32}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct OccupiedFormat {format:String,fence:Fence,entries:Vec<Lease>}
#[derive(Serialize,Deserialize)]
#[serde(deny_unknown_fields)]
struct Stored {leases:OccupiedFormat,next_seq:u64}
pub(crate) fn encode(table:&Table,fence:&Fence)->crate::error::Result<Vec<u8>> {
    if fence.generation!=1 {return Err(crate::error::Error::Invalid("unsupported allocation format".into()));}
    let leases=table.leases.iter().filter(|l|!l.quiet).cloned().collect();
    Ok(serde_json::to_vec(&Stored {leases:OccupiedFormat {format:"local_allocation_authority_v1".into(),fence:fence.clone(),entries:leases},next_seq:table.next_seq})?)
}
pub(crate) fn decode(bytes:&[u8])->crate::error::Result<(Table,Option<Fence>)> {
    let shape:serde_json::Value=serde_json::from_slice(bytes)?;
    if shape.get("leases").is_some_and(|v|v.is_object()) {
        let value:Stored=serde_json::from_slice(bytes)?;
        if value.leases.format!="local_allocation_authority_v1" || value.leases.fence.generation!=1 {
            return Err(crate::error::Error::Invalid("unsupported allocation format".into()));
        }
        let table=Table {leases:value.leases.entries,next_seq:value.next_seq,local_allocations_unresolved:std::sync::Arc::new(std::sync::atomic::AtomicBool::new(true))};
        Ok((table,Some(value.leases.fence)))
    } else {Ok((serde_json::from_slice(bytes)?,None))}
}
#[cfg(unix)]
pub(crate) fn write(parent:&std::fs::File,table:&Table,fence:&Fence)->crate::error::Result<()> {
    use std::{ffi::CString,io::Write,os::fd::{AsRawFd,FromRawFd}};
    let bytes=encode(table,fence)?;
    if bytes.len()>crate::wire::MAX_FRAME {return Err(crate::error::Error::Invalid("allocation format exceeds local frame bound".into()));}
    let name=CString::new(format!("allocation-format-{}",crate::fsutil::random_hex(16)?)).map_err(|_|std::io::Error::other("invalid private temporary name"))?;
    // SAFETY: retained private parent and generated single NUL-terminated leaf;
    // the successful exclusive open transfers ownership exactly once.
    let fd=unsafe {libc::openat(parent.as_raw_fd(),name.as_ptr(),libc::O_WRONLY|libc::O_CREAT|libc::O_EXCL|libc::O_NOFOLLOW|libc::O_CLOEXEC,0o600 as libc::c_uint)};
    if fd<0 {return Err(std::io::Error::last_os_error().into());}
    // SAFETY: successful openat returned a new owned descriptor.
    let mut file=unsafe{std::fs::File::from_raw_fd(fd)};
    if let Err(e)=file.write_all(&bytes).and_then(|_|file.sync_all()) {
        // SAFETY: remove only this invocation's exclusively created temporary leaf.
        unsafe{libc::unlinkat(parent.as_raw_fd(),name.as_ptr(),0);} return Err(e.into());
    }
    // SAFETY: fixed destination and both names relative to the same retained parent.
    if unsafe{libc::renameat(parent.as_raw_fd(),name.as_ptr(),parent.as_raw_fd(),c"leases.json".as_ptr())}!=0 {
        return Err(std::io::Error::last_os_error().into());
    }
    parent.sync_all()?;Ok(())
}
#[cfg(not(unix))]
pub(crate) fn write(_:&std::fs::File,_:&Table,_:&Fence)->crate::error::Result<()> {
    Err(std::io::Error::new(std::io::ErrorKind::Unsupported,"allocation storage unsupported").into())
}
