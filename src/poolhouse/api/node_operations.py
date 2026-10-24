# Generated proposal. No dispatch or authority is implemented. Schema f5b4730d300cd59c6823fcee4dd4a696fb0074ab4ac56fe9dfe549b8001b353e
from . import node_contract as data

OPERATION_TYPES = {
    "jobs.get": (data.JobGetRequest, data.JobView, data.JobError),
    "jobs.cancel": (data.JobCancelRequest, data.JobView, data.JobError),
    "jobs.list": (data.JobListRequest, data.JobListResponse, data.JobError),
    "jobs.output.read": (data.JobOutputRequest, data.JobOutputChunk, data.JobError),
    "agents.get": (data.AgentGetRequest, data.AgentControlSnapshot, data.AgentControlError),
    "agents.pause": (data.AgentControlRequest, data.AgentPauseResult, data.AgentControlError),
    "agents.resume": (data.AgentControlRequest, data.AgentPauseResult, data.AgentControlError),
    "agents.stop": (data.AgentControlRequest, data.AgentStopResult, data.AgentControlError),
    "agents.task.start": (data.AgentTaskStartRequest, data.AgentTaskStartResult, data.AgentControlError),
    "agents.task.stop": (data.AgentTaskStopRequest, data.AgentTaskStopResult, data.AgentControlError),
}
