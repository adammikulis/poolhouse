// Generated proposal. No dispatch or authority is implemented. Schema f5b4730d300cd59c6823fcee4dd4a696fb0074ab4ac56fe9dfe549b8001b353e
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

pub struct AgentsGet;
impl Operation for AgentsGet { type Request = AgentGetRequest; type Response = AgentControlSnapshot; type Error = AgentControlError; const NAME: &'static str = "agents.get"; }

pub struct AgentsPause;
impl Operation for AgentsPause { type Request = AgentControlRequest; type Response = AgentPauseResult; type Error = AgentControlError; const NAME: &'static str = "agents.pause"; }

pub struct AgentsResume;
impl Operation for AgentsResume { type Request = AgentControlRequest; type Response = AgentPauseResult; type Error = AgentControlError; const NAME: &'static str = "agents.resume"; }

pub struct AgentsStop;
impl Operation for AgentsStop { type Request = AgentControlRequest; type Response = AgentStopResult; type Error = AgentControlError; const NAME: &'static str = "agents.stop"; }

pub struct AgentsTaskStart;
impl Operation for AgentsTaskStart { type Request = AgentTaskStartRequest; type Response = AgentTaskStartResult; type Error = AgentControlError; const NAME: &'static str = "agents.task.start"; }

pub struct AgentsTaskStop;
impl Operation for AgentsTaskStop { type Request = AgentTaskStopRequest; type Response = AgentTaskStopResult; type Error = AgentControlError; const NAME: &'static str = "agents.task.stop"; }
