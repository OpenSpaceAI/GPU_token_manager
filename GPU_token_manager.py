import requests
import json
from datetime import datetime
import sched
import time
import logging
from collections import OrderedDict

from manage import *

# 配置日志
logging.basicConfig(
    filename="/zssd/user_token_manager/user_token_manager.log",
    filemode="a",
    level=logging.INFO,
    format="%(asctime)s:%(levelname)s:%(message)s",
)

# 资源价格,单位：token/（GB*hour）
price_3090 = 0.015
# 服务器和查询设置
token_price = {
    "hf-3090-1": price_3090,
    "hf-3090-2": price_3090,
    "hf-3090-3": price_3090,
    "hf-3090-4": price_3090,
    "hf-3090-5": price_3090,
    "hf-3090-6": price_3090,
    "hf-3090-7": price_3090,
    "bj-v100": price_3090,
    "bj-rtx": 0,
    "bj-3090": price_3090,
    "hf-a6000-1": 1.25 * price_3090,
}
# 每小时获取token数量
grant_tokens = 1
# 最大token数量
token_max = 100
# 最小token数量
token_min = -10
# 触发清理时的GPU平均占用率
buzy_trigger_percent = 65
# 执行统计和清理的时间间隔
update_token_interval = 3600  # 令牌更新间隔为 1 小时

token_file = "/zssd/user_token_manager/users_tokens.json"  # 假设的用户数据和令牌数量存储在 JSON 文件中
black_house_file = "/zssd/user_token_manager/black_house.json"  # 被禁用的用户
scheduler = sched.scheduler(time.time, time.sleep)
black_list_max_num = 3

def load_tokens():
    try:
        with open(token_file, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return {}


def save_tokens(users):
    with open(token_file, "w") as file:
        json.dump(users, file)

def get_black_list():
    try:
        with open(black_house_file, "r") as file:
            return json.load(file)
    except FileNotFoundError:
        return []

def save_black_list(black_list):
    with open(black_house_file, "w") as file:
        json.dump(black_list, file)


def query_prometheus(url, params):
    response = requests.get(url, params=params)
    return json.loads(response.content.decode("utf-8"))


def update_usage_and_tokens():
    params = {
        "query": "netdata_nvidia_smi_user_mem_MiB_average",
        "start": datetime.now().timestamp() - update_token_interval,  # 假设过去一小时
        "end": datetime.now().timestamp(),
        "step": "60",
    }
    data = query_prometheus("http://10.1.1.1:9091/api/v1/query_range", params)
    users = load_tokens()

    if data["status"] == "success":
        for series in data["data"]["result"]:
            host_name = series["metric"]["hostname"]
            user_name = series["metric"]["dimension"]
            if user_name not in users:
                users[user_name] = {"token_balance": 100}

            total_cost = 0
            for value in series["values"]:
                usage = float(value[1])
                cost = usage * token_price[host_name]
                total_cost += cost
            # total_cost的单位是token/（MiB*hour*60）,要转化成token/（GB*hour）
            total_cost = total_cost / (1024 * 60)
            new_balance = users[user_name]["token_balance"] - total_cost
            users[user_name]["token_balance"] = max(
                token_min, min(new_balance, token_max)
            )
            logging.info(
                f"Updated Token Balance for {user_name}: {users[user_name]['token_balance']}, GPU:{host_name}, Usage: {usage}, Cost: {total_cost}"
            )

    # 为每位用户每update_token_interval提供grant_tokens点令牌数量
    for user_name in users:
        users[user_name]["token_balance"] = max(
            token_min, min(users[user_name]["token_balance"] + grant_tokens, token_max)
        )
    # 根据用户的令牌数量进行从低到高排序
    users = OrderedDict(sorted(users.items(), key=lambda x: x[1]["token_balance"]))
    save_tokens(users)
    logging.info(f"Updated Users Tokens: {users}")


def check_gpu_utilization_busy():
    params = {
        "query": "netdata_nvidia_smi_gpu_utilization_percentage_average",
        "time": datetime.now().timestamp(),
    }
    users = load_tokens()
    data = query_prometheus("http://10.1.1.1:9091/api/v1/query", params)
    if data["status"] == "success" and data["data"]["result"]:
        percent = 0
        server_num = 0
        for result in data["data"]["result"]:
            host_name = result["metric"]["hostname"]
            # We do not count the 2080 and rtx card for price and usage summary
            if host_name == "hf-217" or host_name == "bj-rtx" or host_name == "bj-2080":
                continue
            logging.info(f"GPU Utilization for {host_name}: {result['value'][1]}%")
            percent += float(result["value"][1])
            server_num += 1
        average_usage = percent / server_num
        logging.info(f"Average GPU Utilization: {average_usage}%")
        if (
            average_usage > buzy_trigger_percent
        ):  # Assume threshold for "full" is buzy_trigger_percent
            return True
        else:
            return False
    return False


def clean_up():
    logging.warning("High GPU utilization detected. Checking for delinquent accounts.")
    users = load_tokens()
    for user_name, user_info in users.items():
        if user_info["token_balance"] < 0:
            logging.info(f"Clearing processes for user {user_name} due to delinquency.")
            disable_user_gpu_usage(user_name)
            if check_gpu_utilization_busy() == False:
                logging.info("GPU is no longer busy. Stopping cleanup.")
                # If GPU is no longer busy, stop cleaning up
                break


def kill_user_process(user_name):
    logging.info(f"Killing processes for user {user_name}")
    # Method to kill processes associated with a user
    for targer in HOST_LIST:
        logging.info(f"Killing processes for user {user_name} on {targer}")
        result = exec_remote(
            targer, "pkill -u {}".format(user_name), sudo=True
        )  # Kill all processes for user
        logging.info(f"Result: {result}")
        # sleep 10 seconds
        time.sleep(10)

def disable_user_gpu_usage(user_name):
    black_list = get_black_list()
    if len(black_list) >= black_list_max_num:
        logging.warning(f"Blacklist is full. We don't disable GPU for user {user_name}")
        return
    logging.info(f"Disabling GPU for user {user_name}")
    # Method to disable GPU for a user
    for targer in HOST_LIST:
        logging.info(f"Disabling GPU for user {user_name} on {targer}")
        for gpu_id in range(8):
            result = exec_remote(
                targer, "setfacl -m u:{}:--- /dev/nvidia{}".format(user_name, gpu_id), sudo=True
            )  # Disable GPU for user
            logging.info(f"Result: {result}")
        # enable GPU for user hf-a6000-1 on card 0
        if targer == "hf-a6000-1":
            exec_remote(
                targer, "setfacl -x u:{} /dev/nvidia0".format(user_name), sudo=True
            )  # Disable GPU for user
    # Add user to the blacklist
    black_list.append(user_name)
    save_black_list(black_list)

def enable_user_gpu_usage(user_name):
    logging.info(f"Enabling GPU for user {user_name}")
    # Method to enable GPU for a user
    for targer in HOST_LIST:
        logging.info(f"Enabling GPU for user {user_name} on {targer}")
        result = exec_remote(
            targer, "setfacl -x u:{} /dev/nvidia*".format(user_name), sudo=True
        )  # Enable GPU for user
        logging.info(f"Result: {result}")
        # sleep 10 seconds
        time.sleep(10)
    # Remove user from the blacklist
    black_list = get_black_list()
    if user_name in black_list:
        black_list.remove(user_name)
        save_black_list(black_list)

def free_users():
    logging.info("Freeing users from the blacklist")
    users = load_tokens()
    black_list = get_black_list()
    for user_name in black_list:
        # if the user in the blacklist have recovered the token balance, free the user
        if user_name in users and users[user_name]["token_balance"] >= 30:
            logging.info(f"Freeing user {user_name}")
            enable_user_gpu_usage(user_name)
  
def main_loop():
    # test kill
    # kill_user_process('cs')
    # enable_user_gpu_usage('cyj')
    update_usage_and_tokens()
    black_list = get_black_list()
    if len(black_list) <= black_list_max_num:
        if check_gpu_utilization_busy():
            clean_up()
    free_users()
    scheduler.enter(
        update_token_interval, 1, main_loop
    )  # Re-schedule this function to run every 30 minutes


def start_scheduling():
    scheduler.enter(
        0, 1, main_loop
    )  # Start immediately and schedule for every 30 minutes
    scheduler.run()


if __name__ == "__main__":
    start_scheduling()
