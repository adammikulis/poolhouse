"""Scheduled jobs: Poolhouse's own commands run later, once the device is free (docs/scheduled-jobs.md).

`who` decides who may schedule, `steps` what a job may run, `code` fixes what it will execute, `runner` is a job's own
process, `spawn` keeps one for every job that has not ended, and `cli` is `poolhouse schedule`. What is kept is
`poolhouse.schedule_record`'s.
"""
