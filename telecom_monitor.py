# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# Repo: https://github.com/Cp0204/ChinaTelecomMonitor
# ConfigFile: telecom_config.json
# Modify: 2026-11-03

"""
任务名称
name: 电信套餐用量监控
定时规则
cron: 0 20 * * *
"""

import os
import sys
import json
import datetime
import calendar

# 兼容青龙
try:
    from telecom_class import Telecom
except:
    print("正在尝试自动安装依赖...")
    os.system("pip3 install pycryptodome requests &> /dev/null")
    from telecom_class import Telecom


CONFIG_DATA = {}
CONFIG_PATH = sys.argv[1] if len(sys.argv) > 1 else "telecom_config.json"
TELECOM_FLUX_PACKAGE = os.environ.get("TELECOM_FLUX_PACKAGE", "true").lower() != "false"
TELECOM_ONLY_WARN = os.environ.get("TELECOM_ONLY_WARN", "false").lower() == "true"
NOTIFY_TITLE = "【电信套餐用量监控】"
MAX_LOGIN_FAIL = 5


# 发送通知消息
def send_notify(title, body):
    try:
        # 导入通知模块
        import notify

        # 如未配置 push_config 则使用青龙环境通知设置
        if CONFIG_DATA.get("push_config"):
            notify.push_config.update(CONFIG_DATA["push_config"])
            notify.push_config["CONSOLE"] = notify.push_config.get("CONSOLE", True)
        notify.send(title, body)
    except Exception as e:
        if e:
            print("发送通知消息失败！")


def usage_status_icon(used, total):
    """流量使用状态图标"""
    if total <= 0:
        return "⚫"  # 无流量
    if used >= total:
        return "🔴"  # 超流量
    # 未超提示进度
    today = datetime.date.today()
    _, days_in_month = calendar.monthrange(today.year, today.month)
    time_progress = today.day / days_in_month
    usage_progress = used / total
    if usage_progress > time_progress * 1.5:
        return "🟠"  # 已超过均匀用量50%
    elif usage_progress > time_progress:
        return "🟡"  # 已超过均匀用量
    else:
        return "🟢"  # 均匀使用范围内


def parse_users_from_env():
    """从 TELECOM_USER 解析账号，支持换行或 & 分隔多个「手机号+密码」"""
    raw = os.environ.get("TELECOM_USER", "")
    users = []
    for line in raw.replace("&", "\n").splitlines():
        line = line.strip()
        if not line:
            continue
        users.append({"phonenum": line[:11], "password": line[11:]})
    return users


def normalize_users(config):
    """归一化账号列表，兼容旧版单个 user 对象；返回 (users, 是否来自环境变量)"""
    env_users = parse_users_from_env()
    from_env = bool(env_users)
    raw = config.get("user")
    if isinstance(raw, dict):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    users = []
    seen = set()
    for item in env_users or raw:
        if not isinstance(item, dict):
            continue
        phonenum = str(item.get("phonenum") or "").strip()
        password = str(item.get("password") or "").strip()
        if not phonenum.isdigit() or len(phonenum) != 11:
            print(f"⚠️ 忽略无效手机号：{phonenum!r}")
            continue
        if not password:
            print(f"⚠️ 忽略未设置密码的账号：{phonenum}")
            continue
        if phonenum in seen:
            print(f"⚠️ 忽略重复账号：{phonenum}")
            continue
        seen.add(phonenum)
        entry = dict(item)
        entry["phonenum"] = phonenum
        entry["password"] = password
        users.append(entry)
    return users, from_env


def normalize_login_info(config):
    """登录信息按手机号存；兼容旧版单账号结构"""
    raw = config.get("login_info") or {}
    if not isinstance(raw, dict):
        return {}
    # 旧版：login_info 直接是单个登录结果
    if raw.get("phonenum"):
        return {str(raw["phonenum"]): raw}
    result = {}
    for phonenum, info in raw.items():
        if isinstance(info, dict):
            info.setdefault("phonenum", str(phonenum))
            result[str(phonenum)] = info
    return result


def normalize_fail_counts(config):
    """登录失败次数按手机号计数；旧版全局计数直接重置"""
    raw = config.get("loginFailTime", {})
    if isinstance(raw, dict):
        counts = {}
        for phonenum, value in raw.items():
            try:
                counts[str(phonenum)] = int(value or 0)
            except (TypeError, ValueError):
                counts[str(phonenum)] = 0
        return counts
    if raw:
        print("⚠️ 旧版 loginFailTime 为全局计数，已重置为按号码计数")
    return {}


def login_account(telecom, user, config, fail_counts):
    """登录单个账号，返回登录信息"""
    phonenum, password = user["phonenum"], user["password"]
    fail_time = fail_counts.get(phonenum, 0)
    if fail_time >= MAX_LOGIN_FAIL:
        raise RuntimeError(
            f"已连续失败{fail_time}次，为避免风控不再执行；"
            f"修正登录信息后，如需重新登录请删除配置中 loginFailTime 的 {phonenum} 键值"
        )
    # 验证过的信任设备，免验证码
    safe_device = {}
    if device_uid := (
        os.environ.get("TELECOM_DEVICE_UID") or config.get("device_uid")
    ):
        # iOS
        safe_device["device_uid"] = device_uid
    elif android_id := (
        os.environ.get("TELECOM_ANDROID_ID") or config.get("android_id")
    ):
        # Android
        safe_device["android_id"] = android_id
    print(f"自动登录：{phonenum}")
    data = telecom.do_login(phonenum, password, **safe_device)
    response = data.get("responseData") or {}
    if response.get("resultCode") == "0000":
        login_info = (response.get("data") or {}).get("loginSuccessResult") or {}
        login_info["phonenum"] = phonenum
        login_info["createTime"] = datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        fail_counts[phonenum] = 0
        print("自动登录：成功")
        return login_info
    fail_counts[phonenum] = fail_time + 1
    raise RuntimeError(
        f"自动登录：已连续失败{fail_time + 1}次，{json.dumps(data, ensure_ascii=False)}"
    )


def build_flux_package(telecom):
    """查询并格式化流量包明细"""
    user_flux_package = telecom.user_flux_package()
    if not user_flux_package or not user_flux_package.get("responseData"):
        print("获取流量包明细：失败")
        return ""
    packages = user_flux_package["responseData"]["data"]["productOFFRatable"][
        "ratableResourcePackages"
    ]
    print("获取流量包明细：成功")
    flux_package_str = ""
    for package in packages:
        package_icon = (
            "🇨🇳"
            if "国内" in package["title"]
            else "📺" if "专用" in package["title"] else "🌎"
        )
        flux_package_str += f"\n{package_icon}{package['title']}\n"
        for product in package["productInfos"]:
            if product["infiniteTitle"]:
                # 无限流量
                flux_package_str += f"""🔹[{product['title']}]{product['infiniteTitle']}{product['infiniteValue']}{product['infiniteUnit']}/无限\n"""
            else:
                flux_package_str += f"""🔹[{product['title']}]{product['leftTitle']}{product['leftHighlight']}{product['rightCommon']}\n"""
    return flux_package_str


def query_account(user, config, login_info, fail_counts):
    """查询单个账号，返回 (summary, 流量包明细字符串, 单位换算函数)"""
    phonenum = user["phonenum"]
    telecom = Telecom()

    info = login_info.get(phonenum)
    if info and info.get("phonenum"):
        print(f"尝试使用缓存登录：{phonenum}")
        telecom.set_login_info(info)
    else:
        info = login_account(telecom, user, config, fail_counts)
        login_info[phonenum] = info
        telecom.set_login_info(info)

    # 获取主要信息
    important_data = telecom.qry_important_data()
    if not important_data.get("responseData"):
        header = important_data.get("headerInfos") or {}
        if header.get("code") == "X201":
            print(f"获取主要信息：失败 {header.get('reason')}，重新登录")
            info = login_account(telecom, user, config, fail_counts)
            login_info[phonenum] = info
            telecom.set_login_info(info)
            important_data = telecom.qry_important_data()
        if not important_data.get("responseData"):
            header = important_data.get("headerInfos") or {}
            raise RuntimeError(
                f"获取主要信息：失败 {header.get('reason') or important_data}"
            )
    print("获取主要信息：成功")

    # 简化主要信息
    try:
        summary = telecom.to_summary(important_data["responseData"]["data"], phonenum)
    except Exception as e:
        raise RuntimeError(
            f"简化主要信息出错，提 Issue 请提供以下信息（隐私打码）：\n\n"
            f"{json.dumps(important_data.get('responseData', {}).get('data', {}), ensure_ascii=False)}\n\n{e}"
        )
    if not summary:
        raise RuntimeError("简化主要信息：返回为空")
    print(f"简化主要信息：{summary}")

    # 获取流量包明细
    flux_package_str = ""
    if TELECOM_FLUX_PACKAGE:
        try:
            flux_package_str = build_flux_package(telecom)
        except Exception as e:
            print(f"获取流量包明细：失败 {e}")

    return summary, flux_package_str, telecom.convert_flow


def build_message(summary, flux_package_str, convert_flow):
    """拼装单个账号的推送正文，返回 (正文, 状态图标)"""
    # 流量字符串
    common_str = (
        f"{convert_flow(summary['commonUse'],'GB',2)} / {convert_flow(summary['commonTotal'],'GB',2)} GB"
        if summary["flowOver"] == 0
        else f"-{convert_flow(summary['flowOver'],'GB',2)} / {convert_flow(summary['commonTotal'],'GB',2)} GB"
    )
    status_icon = usage_status_icon(summary["commonUse"], summary["commonTotal"])
    common_str = f"{common_str} {status_icon}"
    special_str = (
        f"{convert_flow(summary['specialUse'], 'GB', 2)} / {convert_flow(summary['specialTotal'], 'GB', 2)} GB"
        if summary["specialTotal"] > 0
        else ""
    )

    # 基本信息
    notify_str = f"""
📱 手机：{summary['phonenum']}
💰 余额：{round(summary['balance']/100,2)}
📞 通话：{summary['voiceUsage']}{f" / {summary['voiceTotal']}" if summary['voiceTotal']>0 else ''} min
🌐 总流量
  - 通用：{common_str}{f'{chr(10)}  - 专用：{special_str}' if special_str else ''}"""

    # 流量包明细
    if TELECOM_FLUX_PACKAGE:
        notify_str += f"\n\n【流量包明细】\n\n{flux_package_str.strip()}"

    notify_str += f"\n\n查询时间：{summary['createTime']}"
    return notify_str.strip(), status_icon


def main():
    global CONFIG_DATA
    start_time = datetime.datetime.now()
    print(f"===============程序开始===============")
    print(f"⏰ 执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    # 读取配置
    if os.path.exists(CONFIG_PATH):
        print(f"⚙️ 正从 {CONFIG_PATH} 文件中读取配置")
        with open(CONFIG_PATH, "r", encoding="utf-8") as file:
            CONFIG_DATA = json.load(file)
    if not isinstance(CONFIG_DATA, dict):
        CONFIG_DATA = {}

    users, from_env = normalize_users(CONFIG_DATA)
    if not users:
        exit("未设置账号密码，退出")
    login_info = normalize_login_info(CONFIG_DATA)
    fail_counts = normalize_fail_counts(CONFIG_DATA)
    CONFIG_DATA["login_info"] = login_info
    if not from_env:
        # 旧版单个 user 对象在此迁移为列表
        CONFIG_DATA["user"] = users
    # 旧版单个 summary 迁移为按号码存储
    summaries = CONFIG_DATA.get("summary")
    if not isinstance(summaries, dict) or summaries.get("phonenum"):
        summaries = {}

    print(f"📇 共 {len(users)} 个账号：{', '.join(u['phonenum'] for u in users)}")
    print()

    results = []
    for index, user in enumerate(users, 1):
        phonenum = user["phonenum"]
        print(f"--------------- [{index}/{len(users)}] {phonenum} ---------------")
        try:
            summary, flux_package_str, convert_flow = query_account(
                user, CONFIG_DATA, login_info, fail_counts
            )
            summaries[phonenum] = summary
            message, status_icon = build_message(
                summary, flux_package_str, convert_flow
            )
            results.append((phonenum, message, status_icon))
        except Exception as e:
            print(f"❌ {phonenum} 查询失败：{e}")
            results.append((phonenum, f"📱 手机：{phonenum}\n❌ 查询失败：{e}", None))
        print()

    # 先落盘，避免推送异常导致 token / 失败计数丢失
    CONFIG_DATA["loginFailTime"] = fail_counts
    CONFIG_DATA["summary"] = summaries
    update_config()

    # 通知：每个账号单独一条，标题带号码
    print(f"===============推送通知===============")
    for phonenum, message, status_icon in results:
        if TELECOM_ONLY_WARN and status_icon == "🟢":
            print(f"{phonenum} 流量使用在均匀范围内，跳过通知")
            print()
            continue
        send_notify(f"{NOTIFY_TITLE}{phonenum}", message)
        print()


def update_config():
    # 更新配置
    with open(CONFIG_PATH, "w", encoding="utf-8") as file:
        json.dump(CONFIG_DATA, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
