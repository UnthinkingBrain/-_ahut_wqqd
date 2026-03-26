# -*- coding: utf-8 -*-
# @Author：Spance
# @Email: wqqd@spance.xyz
# @Version：v1.8
# @Desc:安徽工业大学考勤系统自动签到


import base64
import json
from datetime import datetime, timezone, timedelta
import hashlib
import logging
import random
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import aiohttp
import asyncio


"""
                更新日志
2026年3月26日 14:32:38：
  修复了学校于2026年3月24日更新导致无法签到的错误，目前项目已可以正常签到
  但为保证签到接口不因为频繁操作导致失败，上调了签到前的等待时间，并加锁限制
  若您为多名用户进行签到，请自行调整重试次数及等待时间
  
2026年3月17日 21:39:52:
  修复了没有在程序结束之前关闭各用户的session，若持久化运行会导致内存泄漏的问题

2026年3月14日 21:57:27:
  添加了异步并发限制，现在无需担心并发太多导致学校服务器压力过大。
  修复了获取经纬度时保存的数据为str类型，导致无法偏置
  
2026年3月14日:
  整体修改成异步执行，在多用户情况下比多线程更快，更合适。
  经过实际测试，异步条件下无限制并发量单次执行不超过15s即可完成百人签到
  添加了获取系统设置的宿舍楼信息接口，创建用户对象时无需设置经纬度。
  为签到的位置添加了随机数，模拟定位偏差。
  每个用户使用专属session持久化上下文调用接口，减少爬虫特征。
  
2026年3月5日:
  更正了签到成功后重复签到会按照签到失败处理的bug。
  
2026年1月3日：
  修改用户类可以直接传入md5加密之后的密码，通过is_encrypted属性选择。
  将generate_sign更新为学校现行版本。
  添加了多个随机UA及访问各接口前添加随机延时，模拟人工操作。
"""


@dataclass
class User:
    # 学号(必须填写)
    student_Id: int
    # 姓名(无需填写，获取token时可自动获取)
    username: str = ''
    # 密码(若未修改考勤系统密码可以留空)
    password: str = "Ahgydx@920"
    # 纬度(无需填写，实时获取)
    latitude: float = 0
    # 经度(无需填写，实时获取)
    longitude: float = 0
    # 用户专属token(无需填写，实时获取)
    token: str = None
    # 签到任务的内部Id(无需填写，实时获取)
    taskId: int = None
    # 宿舍床位编号(无需填写，自动获取)
    room_id:str = ""
    # 当前提供的密码是否为加密之后的
    is_encrypted: int = 0
    # 内部持有的session
    _session = None
    # 对外展示的session
    @property
    def session(self):
        if self._session is None:
            session = aiohttp.ClientSession(headers = {
                'User-Agent': random.choice(UA_LIST),
                'authorization': "Basic Zmx5c291cmNlX3dpc2VfYXBwOkRBNzg4YXNkVURqbmFzZF9mbHlzb3VyY2VfZHNkYWREQUlVaXV3cWU=",
                'Content-Type': "application/json;charset=UTF-8",
                'X-Requested-With': "com.tencent.mm",
                'Origin': "https://xskq.ahut.edu.cn",
                'Referer': f"https://xskq.ahut.edu.cn/wise/pages/ssgl/dormsign?&userId={self.student_Id}"
            })
            self._session = session
        else:
            if self.token: self._session.headers["flysource-auth"] = f"bearer {self.token}"
        return self._session

    async def close(self):
        if self._session:
            await self._session.close()


## *------------------------------------------------------* ##
##             请在此处完成您的配置 ([]内的为可选列表)             ##
## *------------------------------------------------------* ##

# log输出的等级 (logging.[DEBUG,INFO,WARNING,ERROR,CRITICAL])
#       []内的为可选列表，推荐logging.INFO)
LOG_GRADE = logging.DEBUG
# 用户列表，每一个元素是用户对象，具体内容请参考class User
# 本处所给的是四个样例，实际使用时请根据实际填写
USER_LIST = [
    # 使用参考
    # User(259000000),
    # User(259000001, "诸天神佛"),
    # User(259000003, "保我代码", "password"),

    # 此处使用随机学号进行调试，实际情况请使用需要签到学生的学号
    User(random.randint(259024000,259025000))
]
# 单次尝试签到最大尝试次数
MAX_RETRIES = 4
# 单次尝试签到因TOKEN失效最大额外尝试次数
MAX_TOKEN_RETRIES = 3
# 异步并发数限制
MAX_CONCURRENT = 15
# 签到请求锁
SIGN_IN_LOCK = asyncio.Lock()
## *------------------------------------------------------* ##





## *------------------------------------------------------* ##
##                         日志设置区                         ##
## *------------------------------------------------------* ##

# 日志格式设定
formatter = logging.Formatter(
    fmt='%(levelname)s [%(name)s] (%(asctime)s): %(message)s (Line: %(lineno)d [%(filename)s])',
    datefmt='%Y/%m/%d %H:%M:%S'
)

# 获取日志记录器，并设定显示等级
logger = logging.getLogger()
logger.setLevel(LOG_GRADE)

# 添加控制台handler以输出日志
console_handler = logging.StreamHandler(stream=sys.stdout)
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.DEBUG)
logger.addHandler(console_handler)

# 屏蔽第三方库的logging日志
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("dbutils").setLevel(logging.WARNING)
logging.getLogger("yagmail").setLevel(logging.WARNING)
## *------------------------------------------------------* ##





## *------------------------------------------------------* ##
##                         常量声明区                         ##
## *------------------------------------------------------* ##

# 学校考勤系统api_url
API_BASE_URL = "https://xskq.ahut.edu.cn/api"

# 执行完整签到流程所涉及的url
WEB_DICT = {
    # 获取用户token
    "token_api": f"{API_BASE_URL}/flySource-auth/oauth/token",
    # 获取当前签到taskId
    "task_id_api": f"{API_BASE_URL}/flySource-yxgl/dormSignTask/getStudentTaskPage?userDataType=student&current=1&size=15",
    # 获取微信接口配置，确保考勤系统记录中用户是通过微信尝试签到
    "auth_check_api": f"{API_BASE_URL}/flySource-base/wechat/getWechatMpConfig"
                      "?configUrl=https://xskq.ahut.edu.cn/wise/pages/ssgl/dormsign"
                      "?taskId={TASK_ID}&autoSign=1&scanSign=0&userId={STUDENT_ID}",
    # 开启签到的时间窗口
    "apiLog_api": f"{API_BASE_URL}/flySource-base/apiLog/save?menuTitle=%E6%99%9A%E5%AF%9D%E7%AD%BE%E5%88%B0",
    # 获取签到位置
    'get_location_api': f"{API_BASE_URL}/flySource-yxgl/dormSignTask/getTaskByIdForApp"
                        "?taskId={TASK_ID}&signDate={date_str}",
    # 进行晚寝签到(已更新)
    "sign_in_api": f"{API_BASE_URL}/flySource-yxgl/dormSignRecord/stuSign",
    # 获取未签到列表
    "sign_in_result_api": f"{API_BASE_URL}/flySource-yxgl/dormSignStu/getWqdStudentPage"
                          "?taskId={TASK_ID}&xhOrXm=&nowDate={date_str}&userDataType=student&current=1&size=100",
}

# 可选的UA头列表
UA_LIST = [
    "Mozilla/5.0 (Linux; Android 15; MIX Fold 4 Build/TKQ1.240502.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (Linux; Android 15; LYA-AL10 Build/HUAWEILYA-AL10; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/5G Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (Linux; Android 15; SM-S938B Build/TP1A.240205.004; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/128.0.6613.137 Mobile Safari/537.36 MicroMessenger/8.0.61.2660(0x28003D37) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003D29) NetType/WIFI Language/zh_CN",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.61(0x18003D29) NetType/5G Language/zh_CN",
]
## *------------------------------------------------------* ##





## *------------------------------------------------------* ##
##                         功能方法区                         ##
## *------------------------------------------------------* ##

def password_md5(pwd: str) -> str:
    """
    使用 MD5 算法对用户密码进行加密。

    :param pwd: 需加密的明文字段
    :return: 加密后的字符串
    """
    return hashlib.md5(pwd.encode('utf-8')).hexdigest()


def generate_sign(url, token) -> str:
    """
    实时生成指定用户访问指定网页的访问令牌。

    :param url: 所需访问的url
    :param token: user所持有的令牌token
    :return: 指定的网页令牌
    """
    if not token:
        return ''
    parsed_url = urlparse(url)
    api = parsed_url.path + "?sign="
    timestamp = int(time.time() * 1000)
    inner = f"{timestamp}{token}"
    inner_hash = hashlib.md5(inner.encode("utf-8")).hexdigest()
    raw = f"{api}{inner_hash}"
    final_hash = hashlib.md5(raw.encode("utf-8")).hexdigest()
    encoded_time = base64.b64encode(str(timestamp).encode("utf-8")).decode("utf-8")
    return f"{final_hash}1.{encoded_time}"


def get_time() -> dict:
    """
    获取当前时间，并以结构化格式返回。

    :return: 格式化后的时间
    """
    now = time.localtime()
    date = time.strftime("%Y-%m-%d", now)
    current_time = time.strftime("%H:%M:%S", now)
    full_datetime = time.strftime("%Y年%m月%d日 %H:%M:%S", now)
    week_list = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = week_list[now.tm_wday]
    return {
        "date": date,
        "time": current_time,
        "weekday": weekday,
        "full": full_datetime
    }


def generate_header(user: User, url: str = None) -> dict:
    """
    为user访问指定url生成对应的请求头，建议一段时间后更新UA

    :param user: User对象
    :param url: 所需访问的url
    :return: 访问所需的header
    """
    header = {}
    if user.token:
        header['flysource-auth'] = f"bearer {user.token}"
        if url:
            header['flysource-sign'] = generate_sign(url, user.token)
    return header


def generate_params(user: User):
    """
    为user生成获取token时必须的查询参数

    :param user: User对象
    :return: 所需的查询参数字典
    """
    return {
        'tenantId': '000000',
        'username': user.student_Id,
        'password': user.password if user.is_encrypted else password_md5(user.password),
        'type': 'account',
        'grant_type': 'password',
        'scope': 'all'
    }

# 签到接口新参数signCode生成方法
def generate_signCode(timestamp_ms):
    dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc) + timedelta(hours=8)

    week = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    w = week[dt.weekday()]
    m = month[dt.month - 1]
    tz = "GMT+0800 (中国标准时间)"
    time_str = f"{w} {m} {dt.day:02d} {dt.year} {dt.strftime('%H:%M:%S')} {tz}"
    return hashlib.md5(time_str.encode()).hexdigest()

# 签到接口新参数stuTaskId生成方法
def generate_stuTaskId(lat, lng, acc, date, taskId, fileId=""):
    data = {
        "latitude": str(lat),
        "longitude": str(lng),
        "locationAccuracy": str(acc),
        "signDate": date,
        "taskId": taskId,
        "fileId": fileId
    }
    json_str = json.dumps(data, separators=(',', ':'))
    return hashlib.md5(json_str.encode()).hexdigest()

# 签到接口新的提交表单
def generate_data(user: User) -> dict:
    """
    为user生成对应的data用于签到请求时发送

    :param user: User对象
    :return: 规范后的data字典
    """
    signLat = user.latitude + round(random.uniform(-0.01, 0.01), 6)
    signLng = user.longitude + round(random.uniform(-0.01, 0.01), 6)
    locationAccuracy = round(random.uniform(25, 35), 2)
    return {
        "signType": 0,
        "taskId": user.taskId,
        "signLat": signLat,
        "signLng": signLng,
        "locationAccuracy": locationAccuracy,
        "stuTaskId": generate_stuTaskId(signLat,signLng,locationAccuracy,get_time()['date'],user.taskId),
        "scanCode": "",
        "scanType": "",
        "roomId": user.room_id,
        "signKey": user.room_id,
        "signCode": generate_signCode(int(time.time())),
    }


## *------------------------------------------------------* ##





## *------------------------------------------------------* ##
##                       主要功能实现区                       ##
## *------------------------------------------------------* ##

async def sign_in_by_step(user: User, step: int, debug: bool = False) -> dict:
    """
    为指定user执行step步的签到过程，旨在实现错误重试

    :param user: 执行晚寝签到的User对象
    :param step: 当前需要执行的步骤数
    :param debug: 是否处于debug模式
    :return: {success:当前步骤是否完成, msg:错误信息, step:下一次将要进行的步骤}
    """
    # 签到前时间检验
    if not debug:
        now_time = get_time()['time']
        if now_time < '21:20:00':
            logger.error(f'当前时间 {now_time} 未到签到时间，不进行签到')
            return {'success': False,'msg':"未到签到时间",'step': -1}

    # 获取token
    if step == 0:
        logger.info(f"开始为 {user.student_Id} 获取token")
        async with user.session.post(
                url=WEB_DICT["token_api"],
                params=generate_params(user),
                headers=generate_header(user)
        ) as resp:
            token_result = await resp.json()
        logger.debug(f'{user.student_Id} 获取token返回信息 {token_result}')
        if 'refresh_token' in token_result:
            user.token = token_result['refresh_token']
            user.username = token_result['userName']
            logger.info(f"成功为 {user.username}({user.student_Id}) 获取到token")
            logger.debug(f"{user.username}({user.student_Id}) 的token为 {user.token}")
            return {'success': True, 'msg':'','step':step+1}
        else:
            error_desc = token_result.get('error_description','未知错误')
            if "Bad credentials" in error_desc or "用户名或密码错误" in error_desc: error_desc = "密码错误"
            logger.error(f"为 {user.student_Id} 获取token时，出现错误：{error_desc}")
            return {'success': False, 'msg': error_desc, 'step': -1}
    # 获取taskId
    if step == 1:
        logger.info(f"开始为 {user.username}({user.student_Id}) 获取当前签到taskId")
        async with user.session.get(
                url=WEB_DICT['task_id_api'],
                headers=generate_header(user,WEB_DICT['task_id_api'])
        ) as resp:
            task_result = await resp.json()
        logger.debug(f"{user.username}({user.student_Id}) 获取taskId返回信息 {task_result}")
        if task_result['code'] == 200:
            if task_result.get('data', {}).get('records', [{}])[0].get("taskId"):
                user.taskId = task_result.get('data').get('records')[0].get('taskId')
                logger.info(f"为 {user.username}({user.student_Id}) 获取到当前签到的taskId：{user.taskId}")
                return {'success': True, 'msg': '', 'step': step+1}
            else:
                logger.error(f"{user.username}({user.student_Id}) 获取taskId时未在返回信息中解析到taskId字段，请检查{task_result}")
                return {'success': False, 'msg': '未在返回信息中解析到taskId字段', 'step': step}
        else:
            if (("请求未授权" in task_result.get('msg'))
                    or ("缺失身份信息" in task_result.get('msg'))
                    or ('鉴权失败' in task_result.get('msg'))):
                logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            else:
                logger.warning(f"{user.username}({user.student_Id}) 获取taskId时出现问题：{task_result.get('msg')}")
                return {'success': False, 'msg': task_result.get('msg'), 'step': step}
    # 获取微信接口配置
    if step == 2:
        logger.info(f"开始为 {user.username}({user.student_Id}) 获取微信接口配置")
        url =WEB_DICT['auth_check_api'].format(TASK_ID=user.taskId,STUDENT_ID=user.student_Id)
        async with user.session.get(
                url=url,
                headers=generate_header(user,url)
        ) as resp:
            auth_result = await resp.json()
        logger.debug(f"{user.username}({user.student_Id}) 获取微信接口配置返回信息 {auth_result}")
        if auth_result['code'] == 200:
            logger.info(f"为 {user.username}({user.student_Id}) 获取微信接口配置信息成功")
            return {'success': True, 'msg': '', 'step': step+1}
        else:
            if (("请求未授权" in auth_result.get('msg'))
                    or ("缺失身份信息" in auth_result.get('msg'))
                    or ('鉴权失败' in auth_result.get('msg'))):
                logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            else:
                logger.warning(
                    f"{user.username}({user.student_Id}) 获取微信接口配置信息时出现问题：{auth_result.get('msg')}")
                return {'success': False, 'msg': auth_result.get('msg'), 'step': step}
    # 开启时间窗口
    if step == 3:
        logger.info(f"开始为 {user.username}({user.student_Id}) 开启签到时间窗口")
        async with user.session.post(
                url=WEB_DICT["apiLog_api"],
                headers=generate_header(user, WEB_DICT['apiLog_api'])
        ) as resp:
            apiLog_result = resp
            apiLog_text = await apiLog_result.text()
        logger.debug(f"{user.username}({user.student_Id}) 开启签到时间窗口返回信息 {apiLog_text}")
        if apiLog_result.status == 200:
            logger.info(f"为 {user.username}({user.student_Id}) 开启签到时间窗口成功")
            return {'success': True, 'msg': '', 'step': step + 1}
        else:
            logger.warning(
                f"{user.username}({user.student_Id}) 开启签到时间窗口时出现问题")
            return {'success': False, 'msg': "开启签到时间窗口时出现问题", 'step': step}
    # 获取签到的指定位置
    if step == 4:
        logger.info(f"开始获取 {user.username}({user.student_Id}) 签到位置")
        url = WEB_DICT['get_location_api'].format(TASK_ID=user.taskId,date_str=datetime.now().strftime('%Y-%m-%d'))
        async with user.session.get(
                url,
                headers=generate_header(user, url)
        ) as resp:
            location_result = await resp.json()
        logger.debug(f"{user.username}({user.student_Id}) 获取签到位置返回信息 {location_result}")
        if location_result['code'] == 200:
            user.latitude=float(location_result['data'].get('dormitoryRegisterVO',{}).get('locationLat'))
            user.longitude=float(location_result['data'].get('dormitoryRegisterVO',{}).get('locationLng'))
            user.room_id = location_result['data'].get('dormitoryRegisterVO', {}).get("roomId")
            logger.info(f"为 {user.username}({user.student_Id}) 获取签到位置成功")
            return {'success': True, 'msg': '', 'step': step+1}
        else:
            if (("请求未授权" in location_result.get('msg'))
                    or ("缺失身份信息" in location_result.get('msg'))
                    or ('鉴权失败' in location_result.get('msg'))):
                logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                user.token = ''
                return {'success': False, 'msg': 'token失效', 'step': 0}
            return {"success":False,"msg":"","step":step+1}
    # 进行晚寝签到
    if step == 5:
        async with SIGN_IN_LOCK:
            logger.info(f"开始为 {user.username}({user.student_Id}) 晚寝签到")
            sleep_time = round(random.uniform(4,10))
            logger.debug(f"等待时间:{sleep_time}")
            await asyncio.sleep(sleep_time)
            async with user.session.post(
                    url=WEB_DICT["sign_in_api"],
                    json=generate_data(user),
                    headers=generate_header(user,WEB_DICT['sign_in_api'])
            ) as resp:
                sign_in_result = await resp.json()
            logger.debug(f"{user.username}({user.student_Id}) 晚寝签到返回信息 {sign_in_result}")
            if sign_in_result['code'] == 200 or '您今天已完成签到' in sign_in_result['msg']:
                logger.info(f"为 {user.username}({user.student_Id}) 晚寝签到成功")
                return {'success': True, 'msg': '', 'step': step + 1}
            else:
                if (("请求未授权" in sign_in_result.get('msg'))
                        or ("缺失身份信息" in sign_in_result.get('msg'))
                        or ('鉴权失败' in sign_in_result.get('msg'))):
                    logger.warning(f"{user.username}({user.student_Id}) Token失效或未授权，将重试获取Token。")
                    user.token = ''
                    return {'success': False, 'msg': 'token失效', 'step': 0}
                else:
                    if '未到签到时间！' in sign_in_result.get('msg'):
                        logger.warning(
                            f"因当前时间{get_time()['time']}未到签到时间，{user.username}({user.student_Id}) 签到失败")
                        return {'success': False, 'msg': sign_in_result.get('msg'), 'step': -1}
                    logger.warning(
                        f"{user.username}({user.student_Id}) 晚寝签到时出现问题：{sign_in_result.get('msg')}")
                    return {'success': False, 'msg': sign_in_result.get('msg'), 'step': step}

    # 未知情况或传入的step错误
    else:
        logger.debug(f"出现未知错误，当前参数为：user={user.student_Id},step={step}")
        return {'success': False, 'msg': '', 'step': -1}


async def sign_in(user: User, debug: bool = False):
    """
    为单人进行晚寝签到尝试

    :param user: 尝试晚寝签到的User对象
    :param debug: 是否为debug模式，此模式下忽略签到时间限制
    :return: {success:签到结果, data:签到过程中出现的错误}
    """
    logger.info(f"为 {user.username}({user.student_Id}) 尝试执行签到")
    step, retries, token_retries = 0, 0, 0
    error_history = set()

    while retries < MAX_RETRIES and 0 <= step < 6:
        result = await sign_in_by_step(user, step, debug)
        step = result['step']
        if not result['success']:
            error_history.add(result['msg'])
            if step == 0 and token_retries < MAX_TOKEN_RETRIES:
                token_retries += 1
            else:
                retries += 1
        # 添加随机延时，模拟手动操作
        await asyncio.sleep(round(random.uniform(0.5,2),2))

    if step == 6:
        return {'success': True, 'data': error_history}
    else:
        return {'success': False, 'data': error_history}


# 异步执行
async def main():

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def limited_sign_in(user):
        async with semaphore:
            return await sign_in(user, debug=True) # 如需在非签到时间内测试可传入参数debug=True

    async_results = await asyncio.gather(
        *(limited_sign_in(u) for u in USER_LIST))
    await asyncio.gather(*[user.close() for user in USER_LIST])
    return {u.student_Id:result for u,result in zip(USER_LIST,async_results)}


# 异步阻塞执行(串行执行)
# async def main():
#     results = {}
#     for u in USER_LIST:
#         result = await sign_in(u,debug=False) # 如需在非签到时间内测试可传入参数debug=True
#         results[u.student_Id] = result
#     return results

## *------------------------------------------------------* ##



if __name__ == '__main__':
    start_time = time.time()
    results = asyncio.run(main())
    end_time = time.time()
    print(f"本次为 {len(USER_LIST)} 人尝试进行签到，成功人数：{sum([1 if result['success'] else 0 for result in results.values()])}，"
          f"本次任务总耗时 {end_time - start_time:.2f} 秒。\n本次任务签到失败的详细结果如下：")
    for k,v in results.items():
        if not v['success']:
            print(f"\t{k}: {v}")