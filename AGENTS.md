# Project execution environment

- Run every project-related command in the conda environment named `VelaLoom`.
- For non-interactive commands, prefer `conda run -n VelaLoom <command>` so the environment is explicit and reproducible.
- An already activated `VelaLoom` environment is also acceptable. Before relying on activation, verify that `CONDA_DEFAULT_ENV=VelaLoom`.
- This rule applies to dependency installation, Python scripts, tests, builds, linters, formatters, ROS/rosbag tools, and project utilities.
- Do not install project dependencies or execute project tooling in `base`, another conda environment, or the system Python environment.

# Project layout

- Keep development code, scripts, tests, and other executable tooling outside the `urdf` and `rosbag` directories.
- Store shared project scripts in the repository-level `scripts/` directory.
- Treat `urdf` directories as robot description and model assets, and `rosbag` directories as recorded data only.
