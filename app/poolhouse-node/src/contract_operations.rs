// Generated proposal. No dispatch or authority is implemented. Schema e00c39c5a862274de475d5940a9f500a7d7729afe3df21d929c5c92faa7dde90
use super::contract::*;

pub trait Operation { type Request; type Response; type Error; const NAME: &'static str; }

pub struct JobsGet;
impl Operation for JobsGet { type Request = JobGetRequest; type Response = JobView; type Error = JobError; const NAME: &'static str = "jobs.get"; }

pub struct JobsCancel;
impl Operation for JobsCancel { type Request = JobCancelRequest; type Response = JobView; type Error = JobError; const NAME: &'static str = "jobs.cancel"; }

pub struct JobsList;
impl Operation for JobsList { type Request = JobListRequest; type Response = JobListResponse; type Error = JobError; const NAME: &'static str = "jobs.list"; }

pub struct JobsOutputRead;
impl Operation for JobsOutputRead { type Request = JobOutputRequest; type Response = JobOutputChunk; type Error = JobError; const NAME: &'static str = "jobs.output.read"; }
