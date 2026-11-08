from aiogram.fsm.state import State, StatesGroup

class CreateXrayFSM(StatesGroup):
    proto = State()
    remark = State()

class CreateSSHFSM(StatesGroup):
    username = State()
    password = State()
    confirm = State()

class AdminCustomAccountFSM(StatesGroup):
    server_id = State()
    acc_type = State()
    username = State()
    password = State()
    duration = State()
    limit_ip = State()

class AdminUserEditFSM(StatesGroup):
    target_tg_id = State()
    field = State()
    value = State()

class AdminByIdFSM(StatesGroup):
    tg_id = State()
    action = State()
    value = State()

class AdminAccountEditFSM(StatesGroup):
    acc_id = State()
    action = State()
    value = State()

class AdminForceJoinFSM(StatesGroup):
    chat_id = State()
    invite_link = State()

class AdminSettingFSM(StatesGroup):
    key = State()
    value = State()

class AdminBroadcastFSM(StatesGroup):
    message = State()

class AdminRestoreFSM(StatesGroup):
    waiting_file = State()

class AdminServerFSM(StatesGroup):
    name = State()
    domain = State()
    ip = State()
    ssh_port = State()
    ssh_pass = State()
    max_users = State()
    edit_server_id = State()
    edit_max_users = State()
    edit_credentials = State()
    edit_speed_mbps = State()
    edit_quota_gb = State()

class AdminPurgeOverlimitFSM(StatesGroup):
    custom_duration = State()

class BugInjectFSM(StatesGroup):
    custom_bug = State()

class AdminBugAddFSM(StatesGroup):
    value = State()

class ExternalBugFSM(StatesGroup):
    waiting_link = State()
    ready = State()
    custom_bug = State()

class BugCheckerFSM(StatesGroup):
    waiting_host = State()

class SelfManagePassFSM(StatesGroup):
    waiting_pass = State()