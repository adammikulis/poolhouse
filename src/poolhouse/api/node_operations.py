# Generated proposal. No dispatch or authority is implemented. Schema 3f7ee4fb3ed4b13bff6301271c7add44ed4d8ac53aa52929ccf528f080b09fec
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
