"""The one python-build-standalone release a job environment is built from when this device has no Python of that
version, and the sha256 of each build of it: a download that does not match is refused. Moving to a newer release
means editing this file with the digests that release publishes.
"""

RELEASE = "20261124"
SHA256: dict[str, str] = {
    "cpython-3.12.16+20261124-aarch64-apple-darwin-install_only_stripped.tar.gz":
        "9d6d9d0d85feda3f9cb4eb3e048f0fed21ed280feda087927eaa4fbc7124826f",
    "cpython-3.12.16+20261124-aarch64-pc-windows-msvc-install_only_stripped.tar.gz":
        "005f88ec67b0821df5e90406b561c7698a9fba8387c1e5298a862b6639ad65b9",
    "cpython-3.12.16+20261124-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "518e24b37e356775d82d5609cd9d48e27e211381a39ae416381ba696bf76c25c",
    "cpython-3.12.16+20261124-x86_64-apple-darwin-install_only_stripped.tar.gz":
        "6d7ef4efa1adf65f312e6e865040ce03ef8a4bd7e1b45415193295c9ffc0dbdd",
    "cpython-3.12.16+20261124-x86_64-pc-windows-msvc-install_only_stripped.tar.gz":
        "96694b50dcfde13d56e40346ddaa552a9cd650290e9b933cecf83c1d82226960",
    "cpython-3.12.16+20261124-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "7e1adaea26c959d13aa1120be1ae62a1ea1bb52adcb382679fffe9217e22adff",
    "cpython-3.13.18+20261124-aarch64-apple-darwin-install_only_stripped.tar.gz":
        "296f0903cccda15a5c9d3d8393cd2204718aeffd9b095a00f812d75c7b1c0967",
    "cpython-3.13.18+20261124-aarch64-pc-windows-msvc-install_only_stripped.tar.gz":
        "cacad3e38a40d320a9c0ef604efba46b3126239eeaa7cd4ad8765af9382d7e21",
    "cpython-3.13.18+20261124-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "300d388cef58ff23f101e1a41512dc51db9adae136b138c61af9ae1b1d9703a7",
    "cpython-3.13.18+20261124-x86_64-apple-darwin-install_only_stripped.tar.gz":
        "bb16e167ec9e38368118ce3e43ba2c7852fea2cdba16e1852555a8997747f78a",
    "cpython-3.13.18+20261124-x86_64-pc-windows-msvc-install_only_stripped.tar.gz":
        "a455077dd29dd8637b3ab6d745fc0a210d3504a0dd6bd31195ce1e11a81ec9ae",
    "cpython-3.13.18+20261124-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "b56ceb4fc1d8f0c49b7bd4dc91cd176bad7b86a88b9fc509bae6b10b3ee36692",
    "cpython-3.14.10+20261124-aarch64-apple-darwin-install_only_stripped.tar.gz":
        "81fca81fcc8a715288fd12286b66bbdaaf4a0db6818bc91826ddbcffae2ac2cf",
    "cpython-3.14.10+20261124-aarch64-pc-windows-msvc-install_only_stripped.tar.gz":
        "ca7446cb0a7c65782bffecc75f02bc305088fd5ab59c47075d06359372371a68",
    "cpython-3.14.10+20261124-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "2f5df923b202f9054b665c4b4c6ee3fc08bf1ec46cc640bb68ec52aecc658d30",
    "cpython-3.14.10+20261124-x86_64-apple-darwin-install_only_stripped.tar.gz":
        "8198777467d9d3900417dc1cfd9d512956deaebabe293d08aea5cfa5c70d6957",
    "cpython-3.14.10+20261124-x86_64-pc-windows-msvc-install_only_stripped.tar.gz":
        "72d45d2d16c509293999550c684003b0411ab9ec6e473675d1df2af5be050790",
    "cpython-3.14.10+20261124-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz":
        "73ee0629c19e9f87077f318cd50d7e703137c274479ce8e8fd5723e75c86c9bc",
}
