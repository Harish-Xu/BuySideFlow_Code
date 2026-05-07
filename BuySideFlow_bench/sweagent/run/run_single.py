"""[cyan][bold]Run SWE-agent on a single instance taken from github or similar.[/bold][/cyan]

[cyan][bold]=== BASIC OPTIONS ===[/bold][/cyan]

  -h --help           Show help text and exit
  --help_option      Print specific help text and exit
  --config CONFIG     Load additional config files. Use this option multiple times to load
                      multiple files, e.g., --config config1.yaml --config config2.yaml

[cyan][bold]=== EXAMPLES ===[/bold][/cyan]

Basic usage: Run over a [bold][cyan]github issue[/bold][/cyan][green]:

sweagent run --config config/default.yaml --agent.model.name "gpt-4o" \\
    --env.repo.github_url=https://github.com/SWE-agent/test-repo/ \\
    --problem_statement.github_url=https://github.com/SWE-agent/test-repo/issues/1
[/green]

By default this will start a docker container and run the agent in there.
You can set the image with [green]--env.docker.image[/green].

Here's an example that uses [bold][cyan]modal[/bold][/cyan] instead of docker and also a [bold][cyan]local repository[/bold][/cyan]:

[green]sweagent run --config config/default.yaml --agent.model.name "gpt-4o" \\
    --env.deployment.type=modal --env.repo.path /path/to/repo \\
    --problem_statement.path=path/to/problem_statement.md
[/green]
"""

import getpass
import json
from datetime import datetime
import sys
from pathlib import Path
from typing import Optional
from typing_extensions import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from sweagent.agent.agents import AbstractAgent, AgentConfig, get_agent_from_config
from sweagent.agent.problem_statement import (
    EmptyProblemStatement,
    ProblemStatement,
    ProblemStatementConfig,
    SpreadsheetProblemStatement,
    Text2SQLProblemStatement,
)
from sweagent.environment.swe_env import EnvironmentConfig, SWEEnv
from sweagent.run.common import AutoCorrectSuggestion as ACS
from sweagent.run.common import BasicCLI, ConfigHelper, save_predictions
from sweagent.run.hooks.abstract import CombinedRunHooks, RunHook
from sweagent.run.hooks.apply_patch import SaveApplyPatchHook
from sweagent.run.hooks.open_pr import OpenPRConfig, OpenPRHook
from sweagent.text2sql.defaults import (
    DEFAULT_BACKGROUND_PATH,
    DEFAULT_CATALOG_SCHEMA_PATH,
    DEFAULT_FUND_RULES_PATH,
    DEFAULT_SCHEMA_PATH,
    DEFAULT_SELECTION_GUIDANCE_PATH,
)
from sweagent.text2sql.markdown import parse_text2sql_tasks
from sweagent.text2sql.schema import (
    match_requested_tables,
    render_business_background,
    render_focused_schema_catalog,
    render_schema_catalog,
    render_schema_table_index,
    render_table_name_list,
    render_text_asset,
)
from sweagent.utils.config import load_environment_variables
from sweagent.utils.log import add_file_handler, get_logger, remove_file_handler

logger = get_logger("swea-run", emoji="🏃")


class RunSingleActionConfig(BaseModel):
    """Run real-life actions (opening PRs, etc.) if we can solve the issue."""

    # Open a PR with the patch if we can solve the issue
    open_pr: bool = False
    pr_config: OpenPRConfig = Field(default_factory=OpenPRConfig)
    # When working with local repository: Apply patch
    apply_patch_locally: bool = False

    # pydantic config
    model_config = ConfigDict(extra="forbid")


def _get_default_output_dir(output_dir: Path, problem_statement: ProblemStatement, agent: AgentConfig) -> Path:
    if output_dir == Path("DEFAULT"):
        user_id = getpass.getuser()
        problem_id = problem_statement.id
        try:
            model_id = agent.model.id  # type: ignore[attr-defined]
        except AttributeError:
            model_id = "unknown_model"
        config_file = getattr(agent, "_config_files", ["no_config"])[0]
        if isinstance(config_file, Path):
            config_file = config_file.stem
        return Path.cwd() / "trajectories" / user_id / f"{config_file}__{model_id}___{problem_id}"
    return output_dir


class RunSingleConfig(BaseSettings, cli_implicit_flags=False):
    env: EnvironmentConfig = Field(default_factory=EnvironmentConfig, description="Environment options.")
    agent: AgentConfig = Field(description="Agent options.")
    problem_statement: ProblemStatementConfig = Field(
        default_factory=EmptyProblemStatement, description="Problem statement options."
    )
    output_dir: Path = Field(default=Path("DEFAULT"), description="Output directory.")

    actions: RunSingleActionConfig = Field(default_factory=RunSingleActionConfig)

    env_var_path: Path | None = None
    """Path to a .env file to load environment variables from."""

    # pydantic config
    model_config = SettingsConfigDict(extra="forbid", env_prefix="SWE_AGENT_")

    def set_default_output_dir(self) -> None:
        # Needs to be called explicitly, because self._config_files will be setup
        # post-init.
        self.output_dir = _get_default_output_dir(self.output_dir, self.problem_statement, self.agent)

    @classmethod
    def _get_auto_correct(cls) -> list[ACS]:
        return [
            ACS("model", "agent.model.name"),
            ACS("agent.model", "agent.model.name"),
            ACS("model.name", "agent.model.name"),
            ACS("per_instance_cost_limit", "agent.model.per_instance_cost_limit"),
            ACS("model.per_instance_cost_limit", "agent.model.per_instance_cost_limit"),
            ACS("config_file", "config"),
            ACS(
                "data_path",
                help="--data_path is no longer support for SWE-A 1.0. Please check the tutorial and use one of the --problem_statement options, e.g., --problem_statement.github_url or --problem_statement.path",
            ),
            ACS(
                "repo_path",
                help="--repo_path is no longer support for SWE-A 1.0. Please check the tutorial and use one of the --env.repo options, e.g., --env.repo.github_url or --env.repo.path",
            ),
            ACS("repo.path", "env.repo.path"),
        ]


class RunSingle:
    def __init__(
        self,
        env: SWEEnv,
        agent: AbstractAgent,
        problem_statement: ProblemStatement | ProblemStatementConfig,
        *,
        output_dir: Path = Path("."),
        hooks: list[RunHook] | None = None,
        actions: RunSingleActionConfig | None = None,
    ):
        """Note: When initializing this class, make sure to add the hooks that are required by your actions.
        See `from_config` for an example.
        """
        self.logger = get_logger("swea-run", emoji="🏃")
        self._log_handler_ids: list[str] = []
        instance_id = problem_statement.id
        _log_filename_template = f"{instance_id}.{{level}}.log"
        for level in ["trace", "debug", "info"]:
            handler_id = add_file_handler(
                output_dir / instance_id / _log_filename_template.format(level=level),
                level=level,
                id_=f"{instance_id}-{level}",
            )
            self._log_handler_ids.append(handler_id)
        self.env = env
        self.agent = agent
        self.output_dir = output_dir
        self._hooks = []
        if actions is not None:
            actions = RunSingleActionConfig()
        self.actions = actions
        self._chooks = CombinedRunHooks()
        self.problem_statement = problem_statement
        for hook in hooks or []:
            self.add_hook(hook)

    @property
    def hooks(self) -> list[RunHook]:
        return self._chooks.hooks

    @classmethod
    def from_config(cls, config: RunSingleConfig) -> Self:
        load_environment_variables(config.env_var_path)
        config.set_default_output_dir()
        config.output_dir.mkdir(parents=True, exist_ok=True)
        agent = get_agent_from_config(config.agent)
        agent.replay_config = config  # type: ignore[attr-defined]
        self = cls(
            env=SWEEnv.from_config(config.env),
            agent=agent,
            problem_statement=config.problem_statement,
            output_dir=config.output_dir,
            actions=config.actions,
        )
        self.add_hook(SaveApplyPatchHook(apply_patch_locally=config.actions.apply_patch_locally))
        if config.actions.open_pr:
            self.logger.debug("Adding OpenPRHook")
            self.add_hook(OpenPRHook(config.actions.pr_config))
        return self

    def add_hook(self, hook: RunHook) -> None:
        hook.on_init(run=self)
        self._chooks.add_hook(hook)

    def run(self):
        self._chooks.on_start()
        try:
            self.logger.info("Starting environment")
            self.env.start()
            self.logger.info("Running agent")
            self._chooks.on_instance_start(index=0, env=self.env, problem_statement=self.problem_statement)
            output_dir = self.output_dir / self.problem_statement.id
            output_dir.mkdir(parents=True, exist_ok=True)
            if self.agent.replay_config is not None:  # type: ignore[attr-defined]
                (output_dir / "config.yaml").write_text(
                    yaml.dump(self.agent.replay_config.model_dump_json(), indent=2),  # type: ignore[attr-defined]
                    encoding="utf-8",
                )
            result = self.agent.run(
                problem_statement=self.problem_statement,
                env=self.env,
                output_dir=output_dir,
            )
            self._chooks.on_instance_completed(result=result)
            self.logger.info("Done")
            self._chooks.on_end()
            save_predictions(self.output_dir, self.problem_statement.id, result)
        finally:
            self.env.close()
            for handler_id in self._log_handler_ids:
                remove_file_handler(handler_id)


def run_from_config(config: RunSingleConfig):
    RunSingle.from_config(config).run()


def run_from_cli(args: list[str] | None = None):
    if args is None:
        args = sys.argv[1:]
    
    # Check if dataset_path or text2sql_path is provided
    dataset_path = None
    text2sql_path = None
    remaining_args = []
    i = 0
    while i < len(args):
        if args[i] == '--dataset_path' and i + 1 < len(args):
            dataset_path = Path(args[i + 1])
            i += 2
        elif args[i] == '--text2sql_path' and i + 1 < len(args):
            text2sql_path = Path(args[i + 1])
            i += 2
        else:
            remaining_args.append(args[i])
            i += 1
    
    assert __doc__ is not None
    help_text = (  # type: ignore
        __doc__ + "\n[cyan][bold]=== ALL THE OPTIONS ===[/bold][/cyan]\n\n" + ConfigHelper().get_help(RunSingleConfig)
    )
    config = BasicCLI(RunSingleConfig, help_text=help_text).get_config(remaining_args)  # type: ignore
    
    # If dataset_path is provided, process all tasks from dataset.json
    if dataset_path:
        container_data_path = "/mnt/spreadsheet_data"
        
        # Load dataset
        dataset_file = dataset_path / "dataset.json"
        if not dataset_file.exists():
            raise FileNotFoundError(f"Dataset file not found: {dataset_file}")
        
        dataset = json.loads(dataset_file.read_text(encoding="utf-8"))
        
        logger.info(f"Loaded {len(dataset)} tasks from {dataset_file}")
        
        # Setup output directory with config info
        config_file = getattr(config.agent, "_config_files", ["no_config"])[0]
        if isinstance(config_file, Path):
            config_file = config_file.stem
        model_id = str(getattr(config.agent.model, "name", "unknown_model")).replace("/", "_")
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        config_id = f"{len(dataset)}tasks_{model_id}_{run_id}"
        
        # Extract dataset folder name from dataset_path
        dataset_folder_name = dataset_path.name
        
        if config.output_dir == Path("DEFAULT"):
            output_dir = Path.cwd() / "trajectories" / "spreadsheet" / dataset_folder_name / "20tasks_openrouter_google_gemini-3-flash-preview_20260119-112715"
        else:
            output_dir = config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_base = Path.cwd() / "trajectories" / "output_excel" / dataset_folder_name / "20tasks_openrouter_google_gemini-3-flash-preview_20260119-112715"
        output_base.mkdir(parents=True, exist_ok=True)
        container_output_root = "/mnt/spreadsheet_output"
        
        # Configure Docker volumes for data binding
        from swerex.deployment.config import DockerDeploymentConfig
        if isinstance(config.env.deployment, DockerDeploymentConfig):
            if not hasattr(config.env.deployment, 'docker_args') or config.env.deployment.docker_args is None:
                config.env.deployment.docker_args = []
            
            # Check if dataset volume is already bound
            dataset_abs_path = str(dataset_path.resolve())
            volume_bound = any(
                arg == "-v" and container_data_path in str(config.env.deployment.docker_args[i+1] if i+1 < len(config.env.deployment.docker_args) else "")
                for i, arg in enumerate(config.env.deployment.docker_args)
            )
            
            if not volume_bound:
                # Add volume binding: host_path -> container_path (read-only)
                # Using :ro to prevent container modifications from affecting host files
                config.env.deployment.docker_args.extend([
                    "-v",
                    f"{dataset_abs_path}:{container_data_path}:ro",
                ])
                logger.info(f"Configured Docker volume (read-only): {dataset_abs_path} -> {container_data_path}")
            
            # Check if output volume is already bound
            output_abs_path = str(output_base.resolve())
            output_volume_bound = any(
                arg == "-v"
                and container_output_root in str(
                    config.env.deployment.docker_args[i + 1] if i + 1 < len(config.env.deployment.docker_args) else ""
                )
                for i, arg in enumerate(config.env.deployment.docker_args)
            )
            
            if not output_volume_bound:
                config.env.deployment.docker_args.extend([
                    "-v",
                    f"{output_abs_path}:{container_output_root}",
                ])
                logger.info(f"Configured Docker volume: {output_abs_path} -> {container_output_root}")
        
        # Process each task one by ones
        for idx, task_data in enumerate(dataset[2:3]):
            task_id = task_data.get('id', f'task_{idx}')
            logger.info(f"Processing task {idx+1}/{len(dataset)}: {task_id}")
            
            try:
                # Construct file paths
                file_name = f"1_{task_id}_input.xlsx"   
                pdf_file_name = f"1_{task_id}_dataset.pdf"
                host_input_path = dataset_path / task_data['spreadsheet_path'] / file_name
                host_pdf_path = dataset_path / task_data['spreadsheet_path'] / pdf_file_name
                host_output_path = output_base / f"1_{task_id}_output.xlsx"
                host_output_path.parent.mkdir(parents=True, exist_ok=True)
                
                if not host_input_path.exists():
                    logger.warning(f"Spreadsheet file missing for task {task_id}: {host_input_path}")
                    continue

                if not host_pdf_path.exists():
                    logger.warning(f"PDF file missing for task {task_id}: {host_pdf_path}")
                    container_pdf_path = f""
                else:
                    container_pdf_path = f"{container_data_path}/{task_data['spreadsheet_path']}/{pdf_file_name}"
                
                # Container paths
                container_input_path = f"{container_data_path}/{task_data['spreadsheet_path']}/{file_name}"
                container_output_path = f"{container_output_root}/1_{task_id}_output.xlsx"
                
                # Create problem statement from dataset data
                problem_statement = SpreadsheetProblemStatement(
                    instruction=task_data['instruction'],
                    spreadsheet_path=container_input_path,
                    pdf_path=container_pdf_path,
                    instruction_type=task_data['instruction_type'],
                    answer_position=task_data['answer_position'],
                    output_path=container_output_path,
                    id=task_id,
                )
                
                # Create config for this task
                task_config = RunSingleConfig(
                    agent=config.agent,
                    problem_statement=problem_statement,
                    env=config.env,
                    output_dir=output_dir,
                    actions=config.actions,
                )
                
                # Run the task
                run_from_config(task_config)
                
                logger.info(f"Completed task {task_id}")
                
            except Exception as e:
                logger.error(f"Error processing task {task_id}: {e}")
                continue
    elif text2sql_path:
        # ── Text-to-SQL 批量评估：请使用 run-batch 命令 ─────────────────────
        # run_single 仅处理单个任务；批量评估请用:
        #   python -m sweagent run-batch \
        #       --config sweagent/config/text2sql_default.yaml \
        #       --instances.type text2sql \
        #       --instances.path text2sql-schema-filter-main_v8/results/text2sql.markdown
        #
        # 这里保留单任务兼容：取 JSON 第一条运行
        task_file = Path(text2sql_path)
        if not task_file.exists():
            raise FileNotFoundError(f"Text2SQL task file not found: {task_file}")

        if task_file.is_dir() or task_file.suffix.lower() in {".md", ".markdown", ".jsonl"}:
            tasks = parse_text2sql_tasks(task_file)
        else:
            tasks = json.loads(task_file.read_text(encoding="utf-8"))
        if not tasks:
            raise ValueError("Text2SQL task file is empty")

        task = tasks[0]
        logger.info("run-single: running first Text2SQL task only (use run-batch for full benchmark)")

        schema_tables, _ = match_requested_tables(DEFAULT_CATALOG_SCHEMA_PATH, list(task.get("schema_tables", [])))
        focused_schema_catalog = render_focused_schema_catalog(
            DEFAULT_CATALOG_SCHEMA_PATH,
            schema_tables,
            max_columns_per_table=20,
        ) or render_schema_catalog(DEFAULT_CATALOG_SCHEMA_PATH, max_columns_per_table=20)

        problem_statement = Text2SQLProblemStatement(
            question=task["question"],
            mode=task.get("mode", "sql"),
            reference_sql=task.get("sql_code", task.get("reference_sql", "")),
            reference_python=task.get("python_code", task.get("reference_python", "")),
            result_vars=task.get("result_vars", []),
            reference_results=task.get("reference_results", []),
            extra_fields={
                "schema_path": str(DEFAULT_SCHEMA_PATH.resolve()),
                "schema_catalog": focused_schema_catalog,
                "focused_schema_catalog": focused_schema_catalog,
                "schema_table_index": render_schema_table_index(DEFAULT_CATALOG_SCHEMA_PATH),
                "task_schema_tables": ", ".join(schema_tables) or "(not provided)",
                "canonical_question": task.get("canonical_question", task["question"]),
                "desk_style_paraphrases": task.get("desk_style_paraphrases", ""),
                "task_spec": task.get("task_spec", ""),
                "output_contract": task.get("output_contract", ""),
                "evaluation_contract": task.get("evaluation_contract", ""),
                "benchmark_title": task.get("benchmark_title", ""),
                "reference_links": list(task.get("reference_links", [])),
                "reference_artifact_paths": list(task.get("reference_artifact_paths", [])),
                "evaluation_kind": task.get("evaluation_kind", ""),
                "result_dir": task.get("result_dir", ""),
                "schema_columns_per_table": 80,
                "business_background": render_business_background(DEFAULT_BACKGROUND_PATH),
                "selection_guidance": render_text_asset(DEFAULT_SELECTION_GUIDANCE_PATH),
                "fund_rules": render_text_asset(DEFAULT_FUND_RULES_PATH),
                "table_name_list": render_table_name_list(DEFAULT_CATALOG_SCHEMA_PATH),
            },
            id=str(task.get("id", "q1")),
        )
        task_config = RunSingleConfig(
            agent=config.agent,
            problem_statement=problem_statement,
            env=config.env,
            output_dir=config.output_dir,
            actions=config.actions,
        )
        run_from_config(task_config)
    else:
        # Normal single task processing
        # Check if user tries to use SpreadsheetProblemStatement without dataset_path
        if isinstance(config.problem_statement, SpreadsheetProblemStatement):
            raise ValueError(
                "SpreadsheetProblemStatement is only supported with --dataset_path. "
                "Please use --dataset_path to process Excel tasks from dataset.json"
            )
        if isinstance(config.problem_statement, Text2SQLProblemStatement):
            raise ValueError(
                "Text2SQLProblemStatement is only supported with --text2sql_path. "
                "Please use --text2sql_path to process tasks from a markdown/json benchmark file."
            )
        run_from_config(config)


if __name__ == "__main__":
    run_from_cli()
