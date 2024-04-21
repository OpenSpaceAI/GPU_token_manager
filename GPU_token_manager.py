import requests
import json
from datetime import datetime
import sched
import time
import logging
from manage import *
# 配置日志
logging.basicConfig(filename='usage_tokens.log', level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s')

# 服务器和查询设置
token_price = 0.000005
token_max = 100
token_min = -10
token_file = 'users_tokens.json'  # 假设的用户数据和令牌数量存储在 JSON 文件中
scheduler = sched.scheduler(time.time, time.sleep)
buzy_trigger_percent = 30
update_token_interval = 3600  # 令牌更新间隔为 1 小时

def load_tokens():
    try:
        with open(token_file, 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return {}

def save_tokens(users):
    with open(token_file, 'w') as file:
        json.dump(users, file)

def query_prometheus(url, params):
    response = requests.get(url, params=params)
    return json.loads(response.content.decode('utf-8'))

def update_usage_and_tokens():
    params={
        'query': 'netdata_nvidia_smi_user_mem_MiB_average',
        'start': datetime.now().timestamp() - update_token_interval,  # 假设过去一小时
        'end': datetime.now().timestamp(),
        'step': '60'
    }
    data = query_prometheus("http://10.1.1.1:9091/api/v1/query_range", params)
    logging.info(f"Retrieved Data: {data}")
    users = load_tokens()
    
    if data['status'] == 'success':
        for series in data['data']['result']:
            user_id = series['metric']['dimension']
            if user_id not in users:
                users[user_id] = {'username': user_id, 'token_balance': 100}

            total_cost = 0
            for value in series['values']:
                usage = float(value[1])
                cost = usage * token_price
                total_cost += cost

            new_balance = users[user_id]['token_balance'] - total_cost
            users[user_id]['token_balance'] = max(token_min, min(new_balance, token_max))
            logging.info(f"Updated Token Balance for {user_id}: {users[user_id]['token_balance']}, Usage: {usage}, Cost: {total_cost}")

    for user_id in users:
        users[user_id]['token_balance'] = max(token_min, min(users[user_id]['token_balance'] + 1, token_max))

    save_tokens(users)
    logging.info(f"Updated Users Tokens: {users}")
    scheduler.enter(update_token_interval, 1, update_usage_and_tokens)

def check_gpu_utilization():
    params={
        'query': 'netdata_nvidia_smi_gpu_utilization_percentage_average',
        'time': datetime.now().timestamp()
    }
    users = load_tokens()
    data = query_prometheus("http://10.1.1.1:9091/api/v1/query", params)
    if data['status'] == 'success' and data['data']['result']:
        average_usage = sum(float(result['value'][1]) for result in data['data']['result']) / len(data['data']['result'])
        logging.info(f"Average GPU Utilization: {average_usage}%")
        if average_usage > buzy_trigger_percent:  # Assume threshold for "full" is 90%
            logging.warning("High GPU utilization detected. Checking for delinquent accounts.")
            for user_id, user_info in users.items():
                if user_info['token_balance'] < 0:
                    logging.info(f"Clearing processes for user {user_id} due to delinquency.")
                    kill_user_process(user_id)  
    scheduler.enter(update_token_interval, 1, check_gpu_utilization)  # Re-schedule this function to run every 30 minutes

def kill_user_process(user_id):
    logging.info(f"Killing processes for user {user_id}")
    # Method to kill processes associated with a user
    for targer in HOST_LIST:
        exec_remote(targer, "pkill -u {}".format(user_id), sudo=True)  # Kill all processes for user

def start_scheduling():
    scheduler.enter(0, 1, update_usage_and_tokens)
    scheduler.enter(0, 1, check_gpu_utilization)  # Start immediately and schedule for every 30 minutes
    scheduler.run()

if __name__ == "__main__":
    REAL_EXEC = False
    start_scheduling()
