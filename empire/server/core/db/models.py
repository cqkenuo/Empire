import base64
import enum
from typing import Dict

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Sequence,
    String,
    Table,
    Text,
    func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import deferred, relationship
from sqlalchemy_utc import UtcDateTime, utcnow

from empire.server.utils.datetime_util import is_stale

Base = declarative_base()


tasking_download_assc = Table(
    "tasking_download_assc",
    Base.metadata,
    Column("tasking_id", Integer),
    Column("agent_id", String(255)),
    Column("download_id", Integer, ForeignKey("downloads.id")),
    ForeignKeyConstraint(
        ("tasking_id", "agent_id"), ("taskings.id", "taskings.agent_id")
    ),
)

agent_file_download_assc = Table(
    "agent_file_download_assc",
    Base.metadata,
    Column("agent_file_id", Integer, ForeignKey("agent_files.id")),
    Column("download_id", Integer, ForeignKey("downloads.id")),
)


stager_download_assc = Table(
    "stager_download_assc",
    Base.metadata,
    Column("stager_id", Integer, ForeignKey("stagers.id")),
    Column("download_id", Integer, ForeignKey("downloads.id")),
)

# this doesn't actually join to anything atm, but is used for the filtering in api/v2/downloads
upload_download_assc = Table(
    "upload_download_assc",
    Base.metadata,
    Column("download_id", Integer, ForeignKey("downloads.id")),
)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, Sequence("user_id_seq"), primary_key=True)
    username = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    api_token = Column(String(50))
    enabled = Column(Boolean, nullable=False)
    admin = Column(Boolean, nullable=False)
    notes = Column(Text)
    created_at = Column(UtcDateTime, default=utcnow(), nullable=False)
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )

    def __repr__(self):
        return "<User(username='%s')>" % (self.username)


class Listener(Base):
    __tablename__ = "listeners"
    id = Column(Integer, Sequence("listener_id_seq"), primary_key=True)
    name = Column(String(255), nullable=False, unique=True)
    module = Column(String(255), nullable=False)
    listener_type = Column(String(255), nullable=True)
    listener_category = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False)
    options = Column(JSON)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow())

    def __repr__(self):
        return "<Listener(name='%s')>" % (self.name)


class Host(Base):
    __tablename__ = "hosts"
    id = Column(Integer, Sequence("host_id_seq"), primary_key=True)
    name = Column(Text, nullable=False)
    internal_ip = Column(Text)

    # unique check handled differently in mysql and sqlite
    # In base.py, a unique constraint is added for sqlite
    # and a generated column is added for mysql


class Agent(Base):
    __tablename__ = "agents"
    session_id = Column(String(255), primary_key=True, nullable=False)
    name = Column(String(255), nullable=False)
    host_id = Column(Integer, ForeignKey("hosts.id"))
    host = relationship(Host, lazy="joined")
    listener = Column(String(255), nullable=False)
    language = Column(String(255))
    language_version = Column(String(255))
    delay = Column(Integer)
    jitter = Column(Float)
    external_ip = Column(String(255))
    internal_ip = Column(Text)
    username = Column(Text)
    high_integrity = Column(Boolean)
    process_name = Column(Text)
    process_id = Column(Integer)
    hostname = Column(String(255))
    os_details = Column(String(255))
    session_key = Column(String(255))
    nonce = Column(String(255))
    checkin_time = Column(UtcDateTime, default=utcnow())
    lastseen_time = Column(UtcDateTime, default=utcnow(), onupdate=utcnow())
    parent = Column(String(255))
    children = Column(String(255))
    servers = Column(String(255))
    profile = Column(String(255))
    functions = Column(String(255))
    kill_date = Column(String(255))
    working_hours = Column(String(255))
    lost_limit = Column(Integer)
    notes = Column(Text)
    architecture = Column(String(255))
    archived = Column(Boolean, nullable=False)
    proxies = Column(JSON)
    socks = Column(Boolean)
    socks_port = Column(Integer)

    @hybrid_property
    def stale(self):
        return is_stale(self.lastseen_time, self.delay, self.jitter)

    @stale.expression  # todo: this only works for sqlite.
    def stale(cls):
        threshold = 30 + cls.delay + cls.delay * cls.jitter
        seconds_elapsed = (
            func.julianday(utcnow()) - func.julianday(cls.lastseen_time)
        ) * 86400.0
        return seconds_elapsed > threshold

    @property
    def info(self) -> Dict:
        return {
            "ID": self.id,
            "session_id": self.session_id,
            "listener": self.listener,
            "name": self.name,
            "language": self.language,
            "language_version": self.language_version,
            "delay": self.delay,
            "jitter": self.jitter,
            "external_ip": self.external_ip,
            "internal_ip": self.internal_ip,
            "username": self.username,
            "high_integrity": int(self.high_integrity or 0),
            "process_name": self.process_name,
            "process_id": self.process_id,
            "hostname": self.hostname,
            "os_details": self.os_details,
            "session_key": str(self.session_key),
            "nonce": self.nonce,
            "checkin_time": self.checkin_time,
            "lastseen_time": self.lastseen_time,
            "parent": self.parent,
            "children": self.children,
            "servers": self.servers,
            "profile": self.profile,
            "functions": self.functions,
            "kill_date": self.kill_date,
            "working_hours": self.working_hours,
            "lost_limit": self.lost_limit,
            "stale": self.stale,
            "notes": self.notes,
            "architecture": self.architecture,
            "proxy": self.proxy,
        }

    def __repr__(self):
        return "<Agent(name='%s')>" % (self.name)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value


class AgentFile(Base):
    __tablename__ = "agent_files"
    id = Column(Integer, primary_key=True)
    session_id = Column(String(50))
    name = Column(Text, nullable=False)
    path = Column(Text, nullable=False)
    is_file = Column(Boolean, nullable=False)
    parent_id = Column(
        Integer, ForeignKey("agent_files.id", ondelete="CASCADE"), nullable=True
    )
    downloads = relationship("Download", secondary=agent_file_download_assc)


class HostProcess(Base):
    __tablename__ = "host_processes"
    host_id = Column(Integer, ForeignKey("hosts.id"), primary_key=True)
    process_id = Column(Integer, primary_key=True)
    process_name = Column(Text)
    architecture = Column(String(255))
    user = Column(String(255))
    stale = Column(Boolean, default=False)
    agent = relationship(
        Agent,
        lazy="joined",
        primaryjoin="and_(Agent.process_id==foreign(HostProcess.process_id), Agent.host_id==foreign(HostProcess.host_id), Agent.archived == False)",
    )


class Config(Base):
    __tablename__ = "config"
    staging_key = Column(String(255), primary_key=True)
    install_path = Column(Text, nullable=False)
    ip_whitelist = Column(Text, nullable=False)
    ip_blacklist = Column(Text, nullable=False)
    autorun_command = Column(Text, nullable=False)
    autorun_data = Column(Text, nullable=False)
    rootuser = Column(Boolean, nullable=False)
    jwt_secret_key = Column(Text, nullable=False)

    def __repr__(self):
        return "<Config(staging_key='%s')>" % (self.staging_key)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value


class Credential(Base):
    __tablename__ = "credentials"
    id = Column(Integer, Sequence("credential_id_seq"), primary_key=True)
    credtype = Column(String(255))
    domain = Column(Text)
    username = Column(Text)
    password = Column(Text)
    host = Column(Text)
    os = Column(String(255))
    sid = Column(String(255))
    notes = Column(Text)
    created_at = Column(UtcDateTime, default=utcnow(), nullable=False)
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )

    def __repr__(self):
        return "<Credential(id='%s')>" % (self.id)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value


class Download(Base):
    __tablename__ = "downloads"
    id = Column(Integer, Sequence("download_seq"), primary_key=True)
    location = Column(Text, nullable=False)
    filename = Column(Text, nullable=True)
    size = Column(Integer, nullable=True)
    created_at = Column(UtcDateTime, default=utcnow(), nullable=False)
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )

    def get_base64_file(self):
        with open(self.location, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")


class TaskingStatus(str, enum.Enum):
    queued = "queued"
    pulled = "pulled"


class Tasking(Base):
    __tablename__ = "taskings"
    id = Column(Integer, primary_key=True)
    agent_id = Column(String(255), ForeignKey("agents.session_id"), primary_key=True)
    agent = relationship(Agent, lazy="joined", innerjoin=True)
    input = Column(Text)
    input_full = deferred(Column(Text().with_variant(mysql.LONGTEXT, "mysql")))
    output = deferred(
        Column(Text().with_variant(mysql.LONGTEXT, "mysql"), nullable=True)
    )
    # In most cases, this isn't needed and will match output. However, with the filter feature, we want to store
    # a copy of the original output if it gets modified by a filter.
    original_output = deferred(
        Column(Text().with_variant(mysql.LONGTEXT, "mysql"), nullable=True)
    )
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship(User)
    created_at = Column(UtcDateTime, default=utcnow(), nullable=False)
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )
    module_name = Column(Text)
    task_name = Column(Text)
    status = Column(Enum(TaskingStatus), index=True)
    downloads = relationship("Download", secondary=tasking_download_assc)

    def __repr__(self):
        return "<Tasking(id='%s')>" % (self.id)

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value


class Reporting(Base):
    __tablename__ = "reporting"
    id = Column(Integer, Sequence("reporting_id_seq"), primary_key=True)
    name = Column(String(255), nullable=False)
    event_type = Column(String(255))
    message = Column(Text)
    timestamp = Column(UtcDateTime, default=utcnow(), nullable=False)
    taskID = Column(Integer, ForeignKey("taskings.id"))

    def __repr__(self):
        return "<Reporting(id='%s')>" % (self.id)


class Keyword(Base):
    __tablename__ = "keywords"
    id = Column(Integer, Sequence("keyword_seq"), primary_key=True)
    keyword = Column(String(255), unique=True)
    replacement = Column(String(255))
    created_at = Column(UtcDateTime, nullable=False, default=utcnow())
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )

    def __repr__(self):
        return "<KeywordReplacement(id='%s')>" % (self.id)


class Module(Base):
    __tablename__ = "modules"
    id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    enabled = Column(Boolean, nullable=False)
    technique = Column(JSON)
    tactic = Column(JSON)
    software = Column(JSON)


class Profile(Base):
    __tablename__ = "profiles"
    id = Column(Integer, Sequence("profile_seq"), primary_key=True)
    name = Column(String(255), unique=True)
    file_path = Column(String(255))
    category = Column(String(255))
    data = Column(Text, nullable=False)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow())
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )


class Bypass(Base):
    __tablename__ = "bypasses"
    id = Column(Integer, Sequence("bypass_seq"), primary_key=True)
    name = Column(String(255), unique=True)
    authors = Column(JSON)
    code = Column(Text)
    language = Column(String(255))
    created_at = Column(UtcDateTime, nullable=False, default=utcnow())
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )


class Stager(Base):
    __tablename__ = "stagers"
    id = Column(Integer, Sequence("stager_seq"), primary_key=True)
    name = Column(String(255), unique=True)
    module = Column(String(255))
    options = Column(JSON)
    downloads = relationship("Download", secondary=stager_download_assc)
    one_liner = Column(Boolean)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow())
    updated_at = Column(
        UtcDateTime, default=utcnow(), onupdate=utcnow(), nullable=False
    )


class ObfuscationConfig(Base):
    __tablename__ = "obfuscation_config"
    language = Column(String(255), primary_key=True)
    command = Column(Text)
    module = Column(String(255))
    enabled = Column(Boolean)
    preobfuscatable = Column(Boolean)
