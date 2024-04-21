import os
import logging
import pandas as pd
import getpass

# logging.basicConfig(level=logging.DEBUG)

XLSX_PATH = 'user.xlsx'
SUDO_PASSWORD = ''
user_passwd = ''
HOST_LIST = {
    'hf-217': '10.1.91.1',
    'hf-218': '10.1.91.2',
    'hf-3090-1': '10.1.92.1',
    'hf-3090-2': '10.1.92.2',
    'hf-3090-3': '10.1.92.3',
    'hf-3090-4': '10.1.92.4',
    'hf-3090-5': '10.1.92.5',
    'bj-2080': '10.1.93.1',
    'bj-v100': '10.1.93.2',
    'bj-rtx': '10.1.93.3',
    'bj-3090': '10.1.94.1',
    'a6000-1': '10.1.95.1',
}

REAL_EXEC = False

def exec(bash_script: str, sudo=False) -> str:
    if sudo:
        cmd_to_exec_ssh = f"echo '{SUDO_PASSWORD}\n' | sudo -S -p '' bash -c '{bash_script}'"
    else:
        cmd_to_exec_ssh = f"bash -c '{bash_script}'"

    if REAL_EXEC:
        logging.debug(f"Executing: {cmd_to_exec_ssh}")
        os.system(cmd_to_exec_ssh)
        return os.popen(cmd_to_exec_ssh).read()
    else:
        logging.info(f"Dry run: {cmd_to_exec_ssh}")
        return ''

def exec_remote(target: str, bash_script: str, sudo=False) -> str:
    if target == 'master':
        target_ip = '10.1.1.1'
    elif target in HOST_LIST.keys():
        target_ip = HOST_LIST[target]
    else:
        target_ip = target
    if sudo:
        cmd_to_exec_ssh = f"ssh -i /home/wusar/.ssh/lifanwu_ed25519 lifanwu@{target_ip} \"echo user_passwd | sudo -S bash -c '{bash_script}'\""
    else:
        cmd_to_exec_ssh = f"ssh -i /home/wusar/.ssh/lifanwu_ed25519 lifanwu@{target_ip} \"bash -c '{bash_script}'\""

    if REAL_EXEC:
        logging.debug(f"Executing: {cmd_to_exec_ssh}")
        os.system(cmd_to_exec_ssh)
        return os.popen(cmd_to_exec_ssh).read()
    else:
        logging.info(f"Dry run: {cmd_to_exec_ssh}")
        return ''


def get_composed_ssh_pubkey(row: pd.Series):
    keys = []
    for k, v in row.items():
        if k.startswith('ssh_pubkey') and not pd.isna(v):
            keys.append(v)
    if len(keys) == 0:
        return ''
    else:
        return '\n'.join(keys)


def read_user_info():
    df = pd.read_excel(XLSX_PATH)
    info_dict = {}
    for _, row in df.iterrows():
        info = {
            '姓名': row['姓名'],
            'username': row['username'],
            'password': user_passwd,
            'uid': row['uid'],
            'sudo': row['sudo'] == 1,
            'docker': row['docker'] == 1,
            'kvm': row['kvm'] == 1,
            'enabled': row['enabled'] == 1,
            'ssh_pubkey': get_composed_ssh_pubkey(row),
            'home_dir': '/ssd/{}'.format(row['username']),
            'shell': '/bin/bash',
        }
        info_dict[info['username']] = info
    return info_dict

def is_hf_server(target: str):
    return target.startswith('hf') or target.startswith('a6000')

def is_user_existing(target: str, username: str):
    return exec_remote(target, f'cat /etc/passwd | cut -d : -f 1 | grep {username} | wc -l') == '1\n'


def set_password(target: str, username: str, password: str):
    if not is_user_existing(target, username):
        logging.warning(f'{username}: does not exist')
        return
    logging.debug(f'{username}: setting password ...')
    exec_remote(target, f'echo {username}:{password} | chpasswd', sudo=True)
    logging.info(f'{username}: password is set')

def build_zssd_home(info):
    username = info['username']
    uid = info['uid']
    pubkey = info['ssh_pubkey']
    exec_remote('master', f'zfs create -o mountpoint=/zssd/{username} zssd/home/{username}', sudo=True)
    # exec_remote('master', f'rsync -avuPS /zssd/public/skel/.bash_logout /zssd/public/skel/.bashrc /zssd/public/skel/.condarc /zssd/public/skel/.config /zssd/public/skel/.profile /zssd/public/skel/.ssh /zssd/{username}', sudo=True)
    with open('/dev/shm/auth_tmp_12345', 'w') as f:
        f.write(pubkey)
        f.write('\n')
    exec(f'rsync -avuPS /dev/shm/auth_tmp_12345 master:/dev/shm/auth_tmp_12345')
    exec_remote('master', f'cp /dev/shm/auth_tmp_12345 /zssd/{username}/.ssh/authorized_keys && chmod 600 /zssd/{username}/.ssh/authorized_keys', sudo=True)
    exec_remote('master', f'chown -R {uid}:{uid} /zssd/{username}', sudo=True)


def write_ssh_keys(info, target):
    username = info['username']
    pubkey = info['ssh_pubkey']

    # 创建.ssh目录
    exec_remote(target, f'mkdir -p /ssd/{username}/.ssh', sudo=True)

    # 设置.ssh目录的权限
    exec_remote(target, f'chown {username}:{username} /ssd/{username}/.ssh', sudo=True)
    exec_remote(target, f'chmod 700 /ssd/{username}/.ssh', sudo=True)

    # 创建authorized_keys文件并写入公钥
    exec_remote(target, f'echo "{pubkey}" > /ssd/{username}/.ssh/authorized_keys', sudo=True)

    # 设置authorized_keys文件的权限
    exec_remote(target, f'chown {username}:{username} /ssd/{username}/.ssh/authorized_keys', sudo=True)
    exec_remote(target, f'chmod 600 /ssd/{username}/.ssh/authorized_keys', sudo=True)


def create_user(target: str, info: dict):
    username = info['username']
    uid = info['uid']
    home_dir = info['home_dir']
    ssh_pubkey = info['ssh_pubkey']
    shell = info['shell']
    password = user_passwd
    if not info['enabled']:
        logging.warning(f'User {username} is disabled')
        return
    if ssh_pubkey == '' or ssh_pubkey is None:
        logging.warning(f'User {username} has no ssh pubkey, skipping')
        return
    
    # Get the user's extra groups
    extra_groups = []
    if info['sudo']:
        extra_groups.append('sudo')
    if info['docker']:
        extra_groups.append('docker')
    if info['kvm']:
        extra_groups.append('kvm')
    if len(extra_groups) > 0:
        group_str = '-G ' + ','.join(extra_groups)
    else:
        group_str = ''
    logging.info(f'Creating user {username}')


    # Check if user exists
    is_existing = is_user_existing(target, username)
    if is_existing:
        logging.warning(f'User {username} already exists')
        # return

    #  If the server is hf server, create user and link the home directory
    if is_hf_server(target):
        exec_remote(target, f'useradd -d {home_dir} -s {shell} -u {uid} {group_str} {username}', sudo=True)
        exec_remote(target, f'ln -s /zssd/{username} /ssd', sudo=True)
        exec_remote(target, f'chown {uid}:{uid} /ssd/{username}', sudo=True)
    else:
        exec_remote(target, f'useradd -m -d {home_dir} -s {shell} -u {uid} {group_str} {username}', sudo=True)
        exec_remote(target, f'mkdir -p {home_dir}', sudo=True)
        exec_remote(target, f'chown -R {uid}:{uid} {home_dir}', sudo=True)
    set_password(target, username, password)
    write_ssh_keys(info, target)
    logging.info(f'User {username} created')

# def create_det_user(info: dict):
#     username = info['username']
#     uid = info['uid']
#     exec(f'det user create {username}')
#     exec(f'det user link-with-agent-user --agent-user {username} --agent-group {username} --agent-uid {uid} --agent-gid {uid} {username}')

def set_zssd_size(info):
    username = info['username']
    # on master server, run sudo zfs set quota=3T zssd/username
    exec_remote("master", f'zfs set refquota=2T zssd/home/{username}', sudo=True)


if __name__ == '__main__':
    REAL_EXEC = True
    SUDO_PASSWORD = input('Enter the sudo password: ')
    user_info_dict = read_user_info()
    # The user:ayb        cjh  czx      dyw  lifanwu     ljh  lxin  monsoon   qbq  szy  wqj  xuechen   zhuyu  zzr
    # baorunhui  clx  dataset  gy   linxiao     ll   lxj   monsoon2  rh   txj  wx   ydw       zrj
    # blb0607    cs   djc      jh   liuls       lns  lyl   myc       shy  tyy  wy   ywf       zsn
    # changwj    cyh  dtl      jkz  lizhaoyang  lw   lyz   public    sr   wcx  xgx  yxt       zy
    # chz        cyj  dyl      lhk  lizhuoyuan  lwk  mhy   pyw       szx  wjm  xj   zhayixin  zyq
    # _username = [
    #     'ayb', 'cjh', 'czx', 'dyw', 'lifanwu', 'ljh', 'lxin', 'monsoon', 'qbq', 'szy', 'wqj', 'xuechen', 'zhuyu', 'zzr',
    #     'baorunhui', 'clx', 'gy', 'linxiao', 'll', 'lxj', 'rh', 'txj', 'wx', 'ydw', 'zrj',
    #     'cs', 'djc', 'jh', 'liuls', 'lns', 'lyl', 'myc', 'shy', 'tyy', 'wy', 'ywf', 'zsn',
    #     'changwj', 'cyh', 'dtl', 'jkz', 'lizhaoyang', 'lw', 'lyz', 'sr', 'wcx', 'xgx', 'yxt', 'zy',
    #     'chz', 'cyj', 'dyl', 'lhk', 'lizhuoyuan', 'lwk', 'mhy', 'pyw', 'szx', 'wjm', 'xj', 'zhayixin', 'zyq',
    # ]
    _username = input('Enter the username: ')
    _target = input('Enter the target server: ')
    REAL_EXEC = input('Real execution? (y/n)') == 'y'
    for username, info in user_info_dict.items():
        if username != _username:  # 选择用户
            continue
        # set_zssd_size(info)
        for target in HOST_LIST.keys():
            if target != _target:
                continue
            print(f'Creating user {username} on {target}')
            create_user(target, info)  # 创建用户

