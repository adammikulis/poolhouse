# Generated proposal. No dispatch or authority is implemented. Schema e00c39c5a862274de475d5940a9f500a7d7729afe3df21d929c5c92faa7dde90
from . import node_contract as data

OPERATION_TYPES = {
    "jobs.get": (data.JobGetRequest, data.JobView, data.JobError),
    "jobs.cancel": (data.JobCancelRequest, data.JobView, data.JobError),
    "jobs.list": (data.JobListRequest, data.JobListResponse, data.JobError),
    "jobs.output.read": (data.JobOutputRequest, data.JobOutputChunk, data.JobError),
}
