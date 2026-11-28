# What every process of the guest runs with: the tools the setup installed, and short state paths (a Unix socket's
# path, which includes the app's own folder, holds about 100 characters).
export RUSTUP_HOME=/opt/rustup
export CARGO_HOME=/work/cargo
export CARGO_BUILD_JOBS=3
export DEV_TEST_BUDGET=3
export POOLHOUSE_HOME=/work/h
export PYTHONPATH=/work/repo/src
