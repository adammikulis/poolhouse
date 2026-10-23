# Generated proposal. No dispatch or authority is implemented. Schema 85edabd2d8785da6dd55700a1f5c58be18b75d44082505198120758d63b3f1ed
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
