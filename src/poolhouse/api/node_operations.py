# Generated proposal. No dispatch or authority is implemented. Schema e887358b9319b84157e7b99ea7129db0a86daf6c0e61d5e6c80ef4f59bfe79e8
from . import node_contract as data

OPERATION_TYPES = {
    "jobs.get": (data.JobGetRequest, data.JobView, data.JobError),
    "jobs.cancel": (data.JobCancelRequest, data.JobView, data.JobError),
}
