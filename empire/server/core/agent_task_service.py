import json
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session, joinedload, undefer

from empire.server.api.v2.agent.agent_task_dto import (
    ModulePostRequest,
    TaskOrderOptions,
)
from empire.server.api.v2.shared_dto import OrderDirection
from empire.server.common import helpers
from empire.server.core.config import empire_config
from empire.server.core.db import models
from empire.server.core.db.models import TaskingStatus
from empire.server.core.hooks import hooks
from empire.server.core.listener_service import ListenerService
from empire.server.core.module_service import ModuleService

log = logging.getLogger(__name__)


class AgentTaskService(object):
    def __init__(self, main_menu):
        self.main_menu = main_menu

        self.module_service: ModuleService = main_menu.modulesv2
        self.listener_service: ListenerService = main_menu.listenersv2

        # { agent_id: [TemporaryTask] }
        self.temporary_tasks = defaultdict(list)

        self.last_task_lock = threading.Lock()

    @staticmethod
    def get_tasks(
        db: Session,
        agents: List[str] = None,
        users: List[int] = None,
        limit: int = -1,
        offset: int = 0,
        include_full_input: bool = False,
        include_original_output: bool = False,
        include_output: bool = True,
        since: Optional[datetime] = None,
        order_by: TaskOrderOptions = TaskOrderOptions.id,
        order_direction: OrderDirection = OrderDirection.desc,
        status: Optional[TaskingStatus] = None,
        q: Optional[str] = None,
    ):
        query = db.query(
            models.Tasking, func.count(models.Tasking.id).over().label("total")
        )

        if agents:
            query = query.filter(models.Tasking.agent_id.in_(agents))

        if users:
            query = query.filter(models.Tasking.user_id.in_(users))

        query_options = [
            joinedload(models.Tasking.user),
            joinedload(models.Tasking.agent).joinedload(models.Agent.host),
        ]
        if include_full_input:
            query_options.append(undefer("input_full"))
        if include_original_output:
            query_options.append(undefer("original_output"))
        if include_output:
            query_options.append(undefer("output"))
        query = query.options(*query_options)

        if since:
            query = query.filter(models.Tasking.updated_at > since)

        if status:
            query = query.filter(models.Tasking.status == status)

        if q:
            query = query.filter(
                or_(
                    models.Tasking.input.like(f"%{q}%"),
                    models.Tasking.output.like(f"%{q}%"),
                )
            )

        if order_by == TaskOrderOptions.status:
            order_by_prop = models.Tasking.status
        elif order_by == TaskOrderOptions.updated_at:
            order_by_prop = models.Tasking.updated_at
        elif order_by == TaskOrderOptions.agent:
            order_by_prop = models.Tasking.agent_id
        else:
            order_by_prop = models.Tasking.id

        if order_direction == OrderDirection.asc:
            query = query.order_by(order_by_prop.asc())
        else:
            query = query.order_by(order_by_prop.desc())

        if limit > 0:
            query = query.limit(limit).offset(offset)

        results = query.all()

        total = 0 if len(results) == 0 else results[0].total
        results = list(map(lambda x: x[0], results))

        return results, total

    @staticmethod
    def get_task_for_agent(db: Session, agent_id: str, uid: int):
        return (
            db.query(models.Tasking)
            .filter(and_(models.Tasking.agent_id == agent_id, models.Tasking.id == uid))
            .first()
        )

    def get_temporary_tasks_for_agent(self, agent_id: str, clear: bool = True):
        tasks = self.temporary_tasks[agent_id]

        if clear:
            self.temporary_tasks[agent_id] = []

        return tasks

    def create_task_shell(
        self,
        db: Session,
        agent: models.Agent,
        command: str,
        literal: bool = False,
        user_id: int = 0,
    ):
        if literal and not command.startswith("shell"):
            command = f"shell {command}"
        return self.add_task(db, agent, "TASK_SHELL", command, user_id=user_id)

    def create_task_upload(
        self, db: Session, agent: models.Agent, file_data: str, directory: str, user_id
    ):
        data = f"{directory}|{file_data}"
        return self.add_task(db, agent, "TASK_UPLOAD", data, user_id=user_id)

    def create_task_download(
        self, db: Session, agent: models.Agent, path_to_file: str, user_id: int
    ):
        return self.add_task(db, agent, "TASK_DOWNLOAD", path_to_file, user_id=user_id)

    def create_task_script_import(
        self, db: Session, agent: models.Agent, file_data: str, user_id: int
    ):
        if agent.language != "powershell":
            return None, "Only PowerShell agents support script imports"

        # strip out comments and blank lines from the imported script
        file_data = helpers.strip_powershell_comments(file_data)

        return self.add_task(
            db, agent, "TASK_SCRIPT_IMPORT", file_data, user_id=user_id
        )

    def create_task_script_command(
        self, db: Session, agent: models.Agent, command: str, user_id: int
    ):
        return self.add_task(db, agent, "TASK_SCRIPT_COMMAND", command, user_id=user_id)

    def create_task_sysinfo(self, db: Session, agent: models.Agent, user_id: int):
        return self.add_task(db, agent, "TASK_SYSINFO", user_id=user_id)

    def create_task_jobs(self, db: Session, agent: models.Agent, user_id: int):
        return self.add_task(db, agent, "TASK_GETJOBS", user_id=user_id)

    def create_task_kill_job(
        self, db: Session, agent: models.Agent, user_id: int, job_id: str
    ):
        return self.add_task(db, agent, "TASK_STOPJOB", job_id, user_id=user_id)

    def create_task_exit(self, db, agent: models.Agent, current_user_id: int):
        resp, err = self.add_task(db, agent, "TASK_EXIT", user_id=current_user_id)
        agent.archived = True

        # Close socks client
        if (agent.session_id in self.main_menu.agents.socksthread) and agent.stale:
            agent.socks = False
            self.main_menu.agents.socksclient[agent.session_id].shutdown()
            time.sleep(1)
            self.main_menu.agents.socksthread[agent.session_id].kill()
        return resp, err

    def create_task_socks(
        self, db, agent: models.Agent, socks_port, current_user_id: int
    ):
        agent.socks = True
        agent.socks_port = socks_port
        resp, err = self.add_task(db, agent, "TASK_SOCKS", user_id=current_user_id)
        return resp, err

    def create_task_socks_data(self, agent_id: str, data: str):
        return self.add_temporary_task(agent_id, "TASK_SOCKS_DATA", data)

    def create_task_update_comms(
        self, db: Session, agent: models.Agent, new_listener_id: int, user_id: int
    ):
        listener = self.listener_service.get_by_id(db, new_listener_id)

        if not listener:
            return None, f"Listener not found for id {new_listener_id}"
        if listener.module in ["meterpreter", "http_mapi"]:
            return (
                None,
                f"Listener template {listener.module} not eligible for updating comms",
            )

        new_comms = self.listener_service.get_active_listeners()[
            listener.id
        ].generate_comms(listener.options, agent.language)

        self.add_task(
            db, agent, "TASK_UPDATE_LISTENERNAME", listener.name, user_id=user_id
        )
        return self.add_task(
            db, agent, "TASK_SWITCH_LISTENER", new_comms, user_id=user_id
        )

    def create_task_update_sleep(
        self, db: Session, agent: models.Agent, delay: int, jitter: float, user_id: int
    ):
        agent.delay = delay
        agent.jitter = jitter
        if agent.language == "powershell":
            return self.add_task(
                db,
                agent,
                "TASK_SHELL",
                f"Set-Delay {str(delay)} {str(jitter)}",
                user_id=user_id,
            )
        elif agent.language in ["python", "ironpython"]:
            return self.add_task(
                db,
                agent,
                "TASK_CMD_WAIT",
                f"global delay; global jitter; delay={delay}; jitter={jitter}; print('delay/jitter set to {delay}/{jitter}')",
                user_id=user_id,
            )
        elif agent.language == "csharp":
            return self.add_task(
                db,
                agent,
                "TASK_SHELL",
                f"Set-Delay {str(delay)} {str(jitter)}",
                user_id=user_id,
            )
        else:
            return None, "Unsupported language."

    def create_task_update_kill_date(
        self, db: Session, agent: models.Agent, kill_date: str, user_id: int
    ):
        # todo handle different languages
        agent.kill_date = kill_date
        return self.add_task(
            db, agent, "TASK_SHELL", f"Set-KillDate {kill_date}", user_id=user_id
        )

    def create_task_update_working_hours(
        self, db: Session, agent: models.Agent, working_hours: str, user_id: int
    ):
        # todo handle different languages.
        agent.working_hours = working_hours
        return self.add_task(
            db,
            agent,
            "TASK_SHELL",
            f"Set-WorkingHours {working_hours}",
            user_id=user_id,
        )

    def create_task_module(
        self,
        db: Session,
        agent: models.Agent,
        module_req: ModulePostRequest,
        user_id: int,
    ):
        module_req.options["Agent"] = agent.session_id
        resp, err = self.module_service.execute_module(
            db,
            agent,
            module_req.module_id,
            module_req.options,
            module_req.ignore_language_version_check,
            module_req.ignore_admin_check,
        )

        if err:
            return None, err

        return self.add_task(
            db,
            agent,
            task_name=resp["command"],
            task_input=resp["data"],
            module_name=module_req.module_id,
            user_id=user_id,
        )

    def create_task_directory_list(
        self, db: Session, agent: models.Agent, path: str, user_id: int
    ):
        return self.add_task(db, agent, "TASK_DIR_LIST", path, user_id=user_id)

    def create_task_proxy_list(
        self, db: Session, agent: models.Agent, body: Dict, user_id: int
    ):
        agent.proxies = body
        return self.add_task(
            db, agent, "TASK_SET_PROXY", json.dumps(body), user_id=user_id
        )

    class TemporaryTask(BaseModel):
        """
        Fields should match the Task db model, so that we can use the same
        functions to retrieve tasks.
        """

        id: int = 0  # We don't need an ID for these, but it is used in agents.py:1206, so we just initialize it to 0
        agent_id: str
        task_name: str
        input_full: str
        module_name: Optional[str]

    def add_temporary_task(
        self, agent_id: str, task_name, task_input="", module_name: str = None
    ) -> Tuple[Optional[TemporaryTask], Optional[str]]:
        """
        Add a temporary task for the agent to execute. These tasks are not saved in the database,
        since they don't provide any value to end users and can be very write-heavy.
        """
        task = self.TemporaryTask(
            agent_id=agent_id,
            task_name=task_name,
            input_full=task_input,
            module_name=module_name,
        )
        self.temporary_tasks[agent_id].append(task)

        return task, None

    def add_task(
        self,
        db: Session,
        agent: models.Agent,
        task_name,
        task_input="",
        module_name: str = None,
        user_id: int = 0,
    ) -> Tuple[Optional[models.Tasking], Optional[str]]:
        """
        Task an agent. Adapted from agents.py
        """
        if agent.archived:
            return None, f"[!] Agent {agent.session_id} is archived."

        message = f"Tasked {agent.session_id} to run {task_name}"
        log.info(message)
        self.main_menu.agents.save_agent_log(agent.session_id, message)

        pk = (
            db.query(func.max(models.Tasking.id))
            .filter(models.Tasking.agent_id == agent.session_id)
            .first()[0]
        )

        if pk is None:
            pk = 0
        pk = (pk + 1) % 65536

        task = models.Tasking(
            id=pk,
            agent_id=agent.session_id,
            input=task_input[:100],
            input_full=task_input,
            user_id=user_id if user_id else None,
            module_name=module_name,
            task_name=task_name,
            status=TaskingStatus.queued,
        )
        db.add(task)
        db.flush()

        last_task_config = empire_config.debug.last_task
        if last_task_config.enabled:
            with self.last_task_lock:
                location = Path(last_task_config.file)
                location.write_text(task_input)

        hooks.run_hooks(hooks.AFTER_TASKING_HOOK, db, task)

        message = f"Agent {agent.session_id} tasked with task ID {pk}"
        log.info(message)

        return task, None

    @staticmethod
    def delete_task(db: Session, task: models.Tasking):
        db.delete(task)
