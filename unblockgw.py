#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import json
import socket
import shutil
import logging
import argparse
import subprocess
import logging.handlers
from urllib.parse import urlsplit

import requests

try:
    from config import *
except ImportError:
    from default_config import *


elogger = logging.getLogger('stderr')
ologger = logging.getLogger('stdout')


def main():
    """python3 unblockgw.py [-h]
"""
    elogger.debug("")
    elogger.debug(" ".join(sys.argv))

    parser = argparse.ArgumentParser(usage=main.__doc__)
    parser.add_argument('cmd', choices=['gateway', 'surge'])
    args = parser.parse_args(sys.argv[1:2])

    if args.cmd == 'gateway':
        Gateway.execute(sys.argv[2:])
    elif args.cmd == 'surge':
        Surge.execute(sys.argv[2:])


class Gateway(object):

    @classmethod
    def execute(cls, raw_args):
        """python3 unblockgw.py {status,on,off,check,renew,setup,create}

Unblock Gateway 网关命令：
  status                  查看代理状态
  on                      开启代理
  off                     关闭代理
  check <URL/IP/域名>     检查 <URL/IP/域名> 是否走代理
  renew                   更新规则
  setup [--no-ss]         一键配置网关 [--no-ss: 跳过配置 ss-redir]
  restore [--no-ss]       还原路由器为未配置状态 [--no-ss: 跳过还原 ss-redir]
  create                  仅生成 ipset 规则配置文件
"""
        parser = argparse.ArgumentParser(usage=cls.execute.__doc__)
        parser.add_argument(
            'cmd', choices=['status', 'on', 'off', 'check', 'renew', 'setup', 'restore', 'create'])
        args = parser.parse_args(raw_args[0:1])

        if args.cmd == 'create':
            cls.cmd_create()
            return

        # 检查 iptables 和 ipset 命令是否存在
        cls.check_ipset_iptables()

        if args.cmd == 'on':
            cls.cmd_on()
        elif args.cmd == 'off':
            cls.cmd_off()
        elif args.cmd == 'status':
            cls.cmd_status()
        elif args.cmd == 'check':
            cls.cmd_check(raw_args[1:])
        elif args.cmd == 'renew':
            cls.cmd_renew()
        elif args.cmd == 'setup':
            cls.cmd_setup(raw_args[1:])
        elif args.cmd == 'restore':
            cls.cmd_restore(raw_args[1:])

    @classmethod
    def cmd_status(cls):
        """查看 Unblock Youku Gateway 代理状态"""
        cls.check_setup()
        ss_redir_running = cls.check_ss_redir()
        if not ss_redir_running:
            ologger.info("ss-redir 未运行")
            return
        iptables_chn_exists = cls.check_iptables_chn()
        if iptables_chn_exists:
            ologger.info("已开启")
        else:
            ologger.info("已关闭")

    @classmethod
    def cmd_on(cls):
        """开启 Unblock Gateway 代理"""
        cls.check_setup()
        ss_redir_running = cls.check_ss_redir()
        if not ss_redir_running:
            success = cls.start_ss_redir()
            if not success:
                ologger.error("✘ 无法启动 ss-redir")
                sys.exit(1)
        iptables_chn_exists = cls.check_iptables_chn()
        if not iptables_chn_exists:
            cls.add_iptables_chn()
        if ss_redir_running and iptables_chn_exists:
            ologger.info("已经开启")
        else:
            ologger.info("开启成功")

    @classmethod
    def cmd_off(cls):
        """关闭 Unblock Gateway 代理"""
        cls.check_setup()
        iptables_chn_exists = cls.check_iptables_chn()
        if iptables_chn_exists:
            cls.delete_iptables_chn()
            ologger.info("关闭成功")
        else:
            ologger.info("已经关闭")

    @classmethod
    def cmd_check(cls, raw_args):
        """python3 unblockgw.py router check [-h] url
        检查 url 是否走 Unblock Gateway 代理
        """
        parser = argparse.ArgumentParser(usage=cls.cmd_check.__doc__)
        parser.add_argument('url', help="URL / IP / 域名")
        args = parser.parse_args(raw_args)

        cls.check_setup()

        if "://" in args.url:
            domain = urlsplit(args.url).hostname
        else:
            domain = args.url.split('/')[0]
        ip = socket.gethostbyname(domain)

        cmd = "ipset test chn {}".format(ip)
        returncode = subprocess.call(
            cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if returncode == 0:
            ologger.info("{} 走代理".format(ip))
        else:
            ologger.info("{} 不走代理".format(ip))

    @classmethod
    def cmd_renew(cls):
        """更新 Unblock Gateway 规则"""
        cls.check_setup()

        # 生成网关配置文件
        unblock_youku = UnblockYouku()
        cls.create_conf_files(unblock_youku.black_domains)

        # 清空 ipset 的 chn 表
        cmd = "ipset flush chn"
        subprocess.check_call(cmd, shell=True)
        elogger.info("✔ 清空 ipset 的 chn 表：{}".format(cmd))

        # 载入 ipset 规则
        headless_ipset_conf_path = os.path.join(DIR_PATH, "configs/ipset.headless.rules")
        cmd = "ipset restore < {}".format(headless_ipset_conf_path)
        subprocess.check_call(cmd, shell=True)
        elogger.info("✔ 载入 ipset 规则：{}".format(cmd))

        ologger.info("更新成功")

    @classmethod
    def cmd_setup(cls, raw_args):
        """python3 unblockgw.py router setup [-h] [--no-ss]
        Unblock Gateway 一键配置网关
        """
        parser = argparse.ArgumentParser(usage=cls.cmd_setup.__doc__)
        parser.add_argument('--no-ss', action='store_true', help="跳过配置 ss-redir")
        args = parser.parse_args(raw_args)

        # 不跳过配置 ss-redir
        if not args.no_ss:
            # 配置 ss-redir
            cls.setup_ss_redir()

        # 生成网关配置文件
        unblock_youku = UnblockYouku()
        cls.create_conf_files(unblock_youku.black_domains)

        # 清空 ipset 的 chn 表
        cmd = "ipset flush"
        subprocess.check_call(cmd, shell=True)
        elogger.info("✔ 清空 ipset：{}".format(cmd))

        # 配置 ipset 和 iptables
        cls.setup_ipset_iptables()

        # 添加每日更新规则的 cron 定时任务
        cls.add_renew_cron_job()

        ologger.info("配置成功")

    @classmethod
    def cmd_restore(cls, raw_args):
        """python3 unblockchn.py router setup [-h] [--no-ss]
        Unblock Gateway 还原网关为未配置状态
        """
        parser = argparse.ArgumentParser(usage=cls.cmd_restore.__doc__)
        parser.add_argument('--no-ss', action='store_true', help="跳过还原 ss-redir")
        args = parser.parse_args(raw_args)

        # 不跳过还原 ss-redir
        if not args.no_ss:
            # 停止 ss-redir
            ss_redir_running = cls.check_ss_redir()
            if ss_redir_running:
                cls.stop_ss_redir()

            # 从启动脚本里移除 ss-redir 启动命令
            comment = "# ss-redir"
            cls.remove_from_script(SERVICES_START_SCRIPT_PATH, comment)
            elogger.info(f"✔ 从启动脚本里移除 ss-redir 启动命令：{SERVICES_START_SCRIPT_PATH}")

        # # 若 ipset 模板内有其它内容则生成对应配置文件并复制到 jffs
        # # 否则就删除 jffs 中的配置文件
        # ipset_has_conf = cls.create_ipset_conf_file(ipset_rules=None)
        # if ipset_has_conf:
        #     cls.cp_ipset_conf_to_jffs()
        # else:
        #     if os.path.isfile(IPSET_CONF_JFFS_PATH):
        #         os.remove(IPSET_CONF_JFFS_PATH)
        #         elogger.info(f"✔ 删除：{IPSET_CONF_JFFS_PATH}")

        #         # 从启动脚本里移除 ipset 载入命令
        #         comment = "# Load ipset rules"
        #         cls.remove_from_script(NAT_START_SCRIPT_PATH, comment)
        #         elogger.info(f"✔ 从启动脚本里移除 ipset 载入命令：{NAT_START_SCRIPT_PATH}")

        # 删除 iptables 规则
        iptables_chn_exists = cls.check_iptables_chn()
        if iptables_chn_exists:
            cls.delete_iptables_chn()
            elogger.info(f"✔ 删除 iptables 规则：{DELETE_IPTABLES_CHN_CMD}")

        # 从启动脚本里移除 iptables 规则添加命令
        comment = "# Redirect chn ipset to ss-redir"
        cls.remove_from_script(NAT_START_SCRIPT_PATH, comment)
        elogger.info(f"✔ 从启动脚本里移除 iptables 规则添加命令：{NAT_START_SCRIPT_PATH}")

        # 删除 ipset 的 chn 表
        ipset_cmd = "ipset destroy chn"
        try:
            subprocess.check_output(ipset_cmd, shell=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            if "The set with the given name does not exist" not in str(e.stderr):
                raise e
        else:
            elogger.info(f"✔ 删除 ipset 的 chn 表：{ipset_cmd}")

        # 移除每日更新规则的 cron 定时任务
        cls.remove_renew_cron_job()

        # 从启动脚本里移除 xt_set 模块加载命令
        comment = "# Load xt_set module"
        cls.remove_from_script(SERVICES_START_SCRIPT_PATH, comment)
        elogger.info(f"✔ 从启动脚本里移除 xt_set 模块加载命令：{SERVICES_START_SCRIPT_PATH}")

        ologger.info("还原成功")

    @classmethod
    def cmd_create(cls):
        """仅生成 ipset 规则配置文件"""

        # 生成网关配置文件
        unblock_youku = UnblockYouku()
        cls.create_conf_files(unblock_youku.black_domains)

        ologger.info("生成配置文件成功")

    @classmethod
    def create_conf_files(cls, domains):
        """生成网关 ipset 规则配置文件"""

        # 生成 ipset 规则
        ipset_rules = []
        dnsmasq_rules = []
        if domains:
            for domain in domains:
                if re.match(r"\d+\.\d+\.\d+\.\d+", domain):  # IP
                    rule = "add chn {}".format(domain)
                    ipset_rules.append(rule)
                else:  # 域名
                    try:
                        ips = socket.gethostbyname_ex(domain)[2]
                        elogger.debug(f"{domain}:{ips}")
                        for ip in ips:
                            if ip not in ["127.0.0.1", "0.0.0.1"]:
                                rule = f"add chn {ip}"
                                ipset_rules.append(rule)
                    except:
                        pass
        ipset_rules = set(ipset_rules) #remove the same IPs
        # 从模板生成 ipset 规则配置文件 ipset.rules
        cls.create_ipset_conf_file(ipset_rules)

    @classmethod
    def create_ipset_conf_file(cls, ipset_rules):
        """从模板生成 ipset 规则配置文件 ipset.rules"""
        ipset_tpl_path = os.path.join(DIR_PATH, "configs/ipset.rules.tpl")
        if os.path.isfile(ipset_tpl_path):
            with open(ipset_tpl_path, 'r', encoding='utf-8') as f:
                ipset_tpl = f.read()
        else:
            ipset_tpl = "{rules}"
            with open(ipset_tpl_path, 'w', encoding='utf-8') as f:
                f.write(ipset_tpl)
            elogger.info("✔ 生成 ipset 默认配置模板文件：ipset.rules.tpl")

        # 无 ipset 规则 & 无自定义模板内容
        if (not ipset_rules) and (ipset_tpl == "{rules}"):
            return False

        if ipset_rules:
            ipset_rules = "\n".join(ipset_rules)
        else:
            ipset_rules = ""
        ipset_conf = ipset_tpl.format(rules=ipset_rules)

        # 生成包含表创建命令的 ipset 规则配置文件 ipset.rules
        ipset_conf_path = os.path.join(DIR_PATH, "configs/ipset.rules")
        with open(ipset_conf_path, 'w', encoding='utf-8') as f:
            f.write("create chn hash:ip family inet hashsize 1024 maxelem 65536\n")
            f.write(ipset_conf)

        # 生成不包含表创建命令的 ipset 规则配置文件 ipset.headless.rules
        headless_ipset_conf_path = os.path.join(DIR_PATH, "configs/ipset.headless.rules")
        with open(headless_ipset_conf_path, 'w', encoding='utf-8') as f:
            f.write(ipset_conf)

        elogger.info("✔ 生成 ipset 配置文件：ipset.rules & ipset.headless.rules")
        return True

    @classmethod
    def setup_ss_redir(cls):
        """配置 ss-redir"""

        # 生成 ss-redir 配置文件
        conf = SS_REDIR_CONF
        if conf['server'] is None:
            conf['server'] = input("Shadowsocks 服务器地址：").strip()
        if conf['server_port'] is None:
            conf['server_port'] = int(input("Shadowsocks 服务器端口：").strip())
        if conf['password'] is None:
            conf['password'] = input("Shadowsocks 密码：").strip()
        if conf['method'] is None:
            conf['method'] = input("Shadowsocks 加密方法：").strip()
        with open(SS_REDIR_CONF_PATH, 'w', encoding='utf-8') as f:
            json.dump(conf, f, indent=4)
        elogger.info("✔ 保存 ss-redir 配置文件：{}".format(SS_REDIR_CONF_PATH))

        # 启动 ss-redir
        success = cls.start_ss_redir()
        if not success:
            cmd = "{} -c {}".format(SS_REDIR_PATH, SS_REDIR_CONF_PATH)
            elogger.error("✘ 无法启动 ss-redir，请手动运行以下命令查看错误信息：\n{}".format(cmd))
            sys.exit(1)

        # 保存 ss-redir 启动命令到网关的 services-start 启动脚本中
        cmd = "{} -c {} -f {}"
        cmd = cmd.format(SS_REDIR_PATH, SS_REDIR_CONF_PATH, SS_REDIR_PID_PATH)
        comment = "# ss-redir"
        cls.append_to_script(SERVICES_START_SCRIPT_PATH, comment, cmd)
        elogger.info("✔ 保存 ss-redir 启动命令到网关的 services-start 启动脚本中：{}".format(SERVICES_START_SCRIPT_PATH))

    @classmethod
    def setup_ipset_iptables(cls):
        """配置 ipset 和 iptables"""
        # 载入 ipset 规则
        ipset_cmd = "ipset restore < {}".format(IPSET_CONF_PATH)
        try:
            subprocess.check_output(ipset_cmd, shell=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as e:
            if "set with the same name already exists" not in str(e.stderr):
                raise e
        elogger.info("✔ 载入 ipset 规则：{}".format(ipset_cmd))

        # 保存 ipset 载入命令到网关的 nat-start 启动脚本中
        comment = "# Load ipset rules"
        cls.append_to_script(NAT_START_SCRIPT_PATH, comment, ipset_cmd)
        elogger.info("✔ 保存 ipset 载入命令到网关的 nat-start 启动脚本中：{}".format(NAT_START_SCRIPT_PATH))

        # 添加 iptables 规则
        iptables_chn_exists = cls.check_iptables_chn()
        if not iptables_chn_exists:
            cls.add_iptables_chn()
        elogger.info("✔ 添加 iptables 规则：{}".format(ADD_IPTABLES_CHN_CMD))

        # 保存 iptables 添加规则命令到网关的 nat-start 启动脚本中
        comment = "# Redirect chn ipset to ss-redir"
        cmd = 'if [ "$(nvram get unblockgw_on)" = "True" ]; then {}; fi'
        cmd = cmd.format(ADD_IPTABLES_CHN_CMD)
        cls.append_to_script(NAT_START_SCRIPT_PATH, comment, cmd)
        elogger.info("✔ 保存 iptables 规则添加命令到启动脚本中：{}".format(NAT_START_SCRIPT_PATH))

    @classmethod
    def add_renew_cron_job(cls):
        """添加每日更新规则的 cron 定时任务"""
        renew_cmd = "0 {} * * * {} {} renew\r\n"
        unblockgw_path = os.path.realpath(__file__)
        renew_cmd = renew_cmd.format(RENEW_TIME, PYTHON3_PATH, unblockgw_path)
        
        # 写入定时任务到文件中
        ipset_tpl_path = os.path.join(DIR_PATH, "configs/renew_task")
        with open(ipset_tpl_path, 'w', encoding='utf-8') as f:
            f.write(renew_cmd)
        elogger.info("✔ 生成 corn 任务配置模板文件：renew_task")

        cron_cmd = "crontab configs/renew_task"
        try:
            subprocess.check_call(cron_cmd, shell=True)
        except subprocess.CalledProcessError as e:
            elogger.exception(e)
            elogger.warning("✘ 无法添加每日更新规则的定时任务，你需要手动添加以下条目到 crontab 中：\n{}".format(renew_cmd))
            return
        else:
            elogger.info("✔ 定时每日 {} 点更新规则：{}".format(RENEW_TIME, cron_cmd))

        # 保存以上定时命令到网关的 services-start 启动脚本中
        comment = "# unblockgw_renew cron job"
        cls.append_to_script(SERVICES_START_SCRIPT_PATH, comment, cron_cmd)
        elogger.info("✔ 保存定时更新规则命令到网关的 services-start 启动脚本中：{}".format(SERVICES_START_SCRIPT_PATH))

    @classmethod
    def remove_renew_cron_job(cls):
        """移除每日更新规则的 cron 定时任务"""
        cmd = "cru d unblockgw_renew"
        subprocess.check_call(cmd, shell=True)
        elogger.info("✔ 删除每日更新规则的 cron 定时任务：{}".format(cmd))

        # 从启动脚本里移除定时命令
        comment = "# unblockgw_renew cron job"
        cls.remove_from_script(SERVICES_START_SCRIPT_PATH, comment)
        elogger.info("✔ 从启动脚本里移除定时命令：{}".format(SERVICES_START_SCRIPT_PATH))

    @classmethod
    def start_ss_redir(cls):
        """启动 ss-redir"""
        cmd = "{} -c {} -f {}"
        cmd = cmd.format(SS_REDIR_PATH, SS_REDIR_CONF_PATH, SS_REDIR_PID_PATH)
        subprocess.call(cmd, shell=True)
        time.sleep(1)
        is_running = cls.check_ss_redir()
        if is_running:
            elogger.info("✔ 启动 ss-redir：{}".format(cmd))
        return is_running

    @classmethod
    def stop_ss_redir(cls):
        """停止 ss-redir"""
        with open(SS_REDIR_PID_PATH, 'r', encoding='utf-8') as f:
            pid = f.read()
        cmd = "kill {}".format(pid)
        subprocess.check_call(cmd, shell=True)
        elogger.info("✔ 停止 ss-redir：{}".format(cmd))

    @classmethod
    def check_ss_redir(cls):
        """检查 ss-redir 是否运行中"""
        with open(SS_REDIR_PID_PATH, 'r', encoding='utf-8') as f:
            pid = f.read()
        return os.path.exists("/proc/{}".format(pid))

    @classmethod
    def check_setup(cls):
        """检查网关是否配置过"""
        chn_ipset_exists = cls.check_chn_ipset()
        if not chn_ipset_exists:
            ologger.error("✘ 网关未正确配置，请先运行以下命令进行配置：\npython3 unblockgw.py setup")
            sys.exit(1)

    @classmethod
    def check_ipset_iptables(cls):
        """检查 iptables 和 ipset 命令是否存在"""
        iptables_exists = cls.check_command('iptables')
        ipset_exists = cls.check_command('ipset')
        if not (iptables_exists and ipset_exists):
            d = {'iptables': iptables_exists, 'ipset': ipset_exists}
            missing = [k for k in d if not d[k]]
            ologger.error("✘ 运行环境不支持 {} 命令".format(" 和 ".join(missing)))
            sys.exit(1)

    @classmethod
    def check_chn_ipset(cls):
        """检查 ipset 是否有 chn 表"""
        cmd = "ipset list chn"
        returncode = subprocess.call(
            cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return returncode == 0

    @classmethod
    def add_iptables_chn(cls):
        """iptables 添加 chn ipset 规则"""
        subprocess.check_call(ADD_IPTABLES_CHN_CMD, shell=True)

    @classmethod
    def delete_iptables_chn(cls):
        """iptables 删除 chn ipset 规则"""
        subprocess.check_call(DELETE_IPTABLES_CHN_CMD, shell=True)

    @classmethod
    def check_iptables_chn(cls):
        """检查 iptables 是否有 chn ipset 规则"""
        returncode = subprocess.call(
            CHECK_IPTABLES_CHN_CMD, shell=True, stderr=subprocess.DEVNULL)
        return returncode == 0

    @classmethod
    def append_to_script(cls, script_path, comment, cmd):
        """添加命令到脚本"""
        if os.path.isfile(script_path):
            with open(script_path, 'r', encoding='utf-8') as f:
                scpt = f.read()
        else:
            scpt = "#!/bin/sh\n"
        if comment not in scpt:
            scpt += "\n" + comment + "\n" + cmd + "\n"
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(scpt)
        cmd = "chmod a+rx {}".format(script_path)
        subprocess.check_call(cmd, shell=True)

    @classmethod
    def remove_from_script(cls, script_path, comment):
        """从脚本中移除命令"""
        if not os.path.isfile(script_path):
            return
        with open(script_path, 'r', encoding='utf-8') as f:
            scpt = f.read()
        pattern = r"\n" + comment + r"\n.+\n?"
        scpt = re.sub(pattern, "", scpt)
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write(scpt)

    @classmethod
    def check_command(cls, command):
        """检查命令是否存在"""
        returncode = subprocess.call(
            ["which", command],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if returncode == 0:
            return True
        else:
            return False

class UnblockYouku(object):

    def __init__(self):
        super(UnblockYouku, self).__init__()
        self.source = requests.get(UNBLOCK_YOUKU_URLSJS_URL).text
        self._black_urls = None
        self._white_urls = None
        self._black_domains = None
        self._white_domains = None

    @property
    def black_urls(self):
        """URLs 黑名单"""
        if self._black_urls is not None:
            return self._black_urls

        header_urls = self.extract('HEADER_URLS')
        proxy_urls = self.extract('PROXY_URLS')

        self._black_urls = header_urls + proxy_urls
        self._black_urls = list(set(self._black_urls))
        self._black_urls.sort()
        elogger.info("black list：{}".format(len(self._black_urls)))

        return self._black_urls

    @property
    def white_urls(self):
        """URLs 白名单"""
        if self._white_urls is not None:
            return self._white_urls

        proxy_bypass_urls = self.extract('PROXY_BYPASS_URLS')

        self._white_urls = proxy_bypass_urls
        self._white_urls = list(set(self._white_urls))
        self._white_urls.sort()
        elogger.info("white list：{}".format(len(self._white_urls)))

        return self._white_urls

    @property
    def black_domains(self):
        """域名黑名单"""
        if self._black_domains is not None:
            return self._black_domains
        
        self._black_domains = []
        for url in self.black_urls:
            domain = urlsplit(url).hostname
            self._black_domains.append(domain)

        extra_domains = self.read_extra()
        for domain in extra_domains:
            domain = domain.replace("\n", "")
            self._black_domains.append(domain)

        elogger.info(f"black domain:{len(self._black_domains)}")
        elogger.info(self._black_domains)

        self._black_domains = list(set(self._black_domains))

        self._black_domains.sort(key=lambda s: s[::-1], reverse=True)

        elogger.info(f"black domain:{len(self._black_domains)}")
        elogger.info(self._black_domains)
        return self._black_domains

    @property
    def white_domains(self):
        """域名白名单"""
        if self._white_domains is not None:
            return self._white_domains

        self._white_domains = []
        for url in self.white_urls:
            domain = urlsplit(url).hostname
            self._white_domains.append(domain)

        self._white_domains = list(set(self._white_domains))
        self._white_domains.sort(key=lambda s: s[::-1], reverse=True)

        return self._white_domains

    def extract(self, name):
        """从 Unblock Youku 的 urls.js 中提取指定的 URL 列表"""
        pattern = "export\\sconst\\s{}\\s*=.+?(\\[.+?\\])".format(name)
        match = re.search(pattern, self.source, re.DOTALL)
        if not match:
            elogger.error("✘ 从 Unblock Youku 提取 {} 规则失败".format(name))
            sys.exit(1)
        s = match.group(1)
        s = s.replace("'", '"')  # 替换单引号为双引号
        s = re.sub(r"(?<!:)//.+", "", s)  # 去除注释
        s = re.sub(r",\s*\]", "\n]", s)  # 去除跟在最后一个元素后面的逗号
        urls = json.loads(s)
        return urls

    def read_extra(self):
        extra_path = os.path.join(DIR_PATH, "configs/extra.txt")
        extra_urls = []
        if os.path.isfile(extra_path):
            with open(extra_path, 'r', encoding='utf-8') as f:
                extra_urls = f.readlines()      
        
        return extra_urls


class Surge(object):

    @classmethod
    def execute(cls, raw_args):
        """python3 unblockgw.py surge [-h] [-d DST]
        Unblock Gateway
        生成 Surge 配置文件
        """
        parser = argparse.ArgumentParser(usage=cls.execute.__doc__)
        parser.add_argument('-u', '--url', action='store_true', help="生成基于 URL 正则表达式的规则（默认基于域名）")
        parser.add_argument('-r', '--ruleset', action='store_true', help="生成 Surge ruleset 文件")
        parser.add_argument('-d', '--dst', help="保存生成的文件到此目录")
        args = parser.parse_args(raw_args)

        unblock_youku = UnblockYouku()

        if args.url:  # 基于 URL 生成规则
            black_urls = unblock_youku.black_urls
            white_urls = unblock_youku.white_urls
            rules = cls.url_rules(black_urls, white_urls)
        else:  # 基于域名生成规则
            black_domains = unblock_youku.black_domains
            rules = cls.domain_rules(black_domains)

        if args.ruleset:  # 生成 Surge ruleset 文件
            cls.create_ruleset_file(rules)
        else:  # 生成 Surge 规则配置文件
            has_conf = cls.create_conf_files(rules)
            if not has_conf:
                elogger.error("✘ 目录下不存在后缀为 .conf.tpl 的 Surge 配置模板文件（忽略 sample_surge.conf.tpl）")
                sys.exit(1)

        # 保存生成的文件到 args.dst
        if args.dst:
            if not os.path.exists(args.dst):
                elogger.error(f"✘ 目的地文件夹不存在：{args.dst}")
                sys.exit(1)
            if not os.path.isdir(args.dst):
                elogger.error(f"✘ 目的地路径非文件夹：{args.dst}")
                sys.exit(1)
            if args.ruleset:  # 复制 Surge ruleset 文件
                cls.cp_ruleset_file(args.dst)
            else:  # 复制 Surge 规则配置文件
                cls.cp_conf_files(args.dst)

    @classmethod
    def url_rules(cls, black_urls, white_urls):
        """生成基于 URL 正则表达式的规则"""
        black_rules = cls.urls_to_rules(black_urls)
        white_urls = cls.urls_to_rules(white_urls)
        rules = {
            'black': black_rules,
            'white': white_urls
        }
        return rules

    @classmethod
    def urls_to_rules(cls, urls):
        """将 urls 转换为 Surge 规则"""
        rules = []
        for url in urls:
            if url.startswith('http://'):  # http
                reg_url = re.escape(url)
                reg_url = reg_url.replace("\\*", ".*")
                reg_url = "^" + reg_url
                rule = f"URL-REGEX,{reg_url}"
            else:  # https
                domain = urlsplit(url).hostname
                if domain.startswith("*."):  # DOMAIN-SUFFIX
                    domain = domain.replace("*.", "", 1)
                    rule = f"AND,((DOMAIN-SUFFIX,{domain}),(DEST-PORT,443))"
                else:  # DOMAIN
                    rule = f"AND,((DOMAIN,{domain}),(DEST-PORT,443))"
            rules.append(rule)
        return rules

    @classmethod
    def domain_rules(cls, black_domains):
        """生成基于域名的规则"""
        black_rules = []
        for domain in black_domains:
            if domain.startswith("*."):  # DOMAIN-SUFFIX
                domain = domain.replace("*.", "", 1)
                rule = f"DOMAIN-SUFFIX,{domain}"
            else:  # DOMAIN
                rule = f"DOMAIN,{domain}"
            black_rules.append(rule)
        rules = {
            'black': black_rules,
            'white': []
        }
        return rules

    @classmethod
    def create_conf_files(cls, rules):
        """从模板生成 Surge 规则配置文件"""
        white_rules = rules['white']
        white_rules = [rule + "," + "DIRECT" for rule in white_rules]
        black_rules = rules['black']
        black_rules = [rule + "," + SURGE_PROXY_GROUP_NAME for rule in black_rules]

        rules = "\n".join(white_rules + black_rules)

        has_conf = False

        for name in os.listdir(SURGE_DIR_PATH):
            if not name.endswith(".conf.tpl"):
                continue
            if name.startswith("._"):
                continue
            if name == "sample_surge.conf.tpl":  # 跳过样例模板
                continue
            tpl_path = os.path.join(SURGE_DIR_PATH, name)
            with open(tpl_path, 'r', encoding='utf-8') as f:
                tpl = f.read()
            conf_name = name[:-4]
            conf_path = os.path.join(SURGE_DIR_PATH, conf_name)
            conf = tpl.format(rules=rules)
            with open(conf_path, 'w', encoding='utf-8') as f:
                f.write(conf)
            has_conf = True
            elogger.info(f"✔ 生成 Surge 配置文件（surge 目录）：{conf_name}")

        return has_conf

    @classmethod
    def create_ruleset_file(cls, rules):
        """生成 Surge ruleset 文件"""
        rules = "\n".join(rules['black'])
        ruleset_file_path = os.path.join(SURGE_DIR_PATH, "unblockchn.surge.ruleset")
        with open(ruleset_file_path, 'w', encoding='utf-8') as f:
            f.write(rules)
        elogger.info("✔ 生成 Surge ruleset 文件（surge 目录）：unblockchn.surge.ruleset")

    @classmethod
    def cp_conf_files(cls, dst):
        """复制目录下的 Surge 配置文件到 dst 文件夹"""
        for name in os.listdir(SURGE_DIR_PATH):
            if not name.endswith('.conf'):
                continue
            if name.startswith("._"):
                continue
            src_path = os.path.join(SURGE_DIR_PATH, name)
            dst_path = os.path.join(dst, name)
            shutil.copy2(src_path, dst_path)
            elogger.info(f"✔ 保存 Surge 配置文件到：{dst_path}")

    @classmethod
    def cp_ruleset_file(cls, dst):
        """复制目录下的 Surge ruleset 文件到 dst 文件夹"""
        name = "unblockchn.surge.ruleset"
        src_path = os.path.join(SURGE_DIR_PATH, name)
        dst_path = os.path.join(dst, name)
        shutil.copy2(src_path, dst_path)
        elogger.info(f"✔ 保存 Surge ruleset 文件到：{dst_path}")


def init_logging():
    """日志初始化"""

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.NOTSET)

    # 输出日志到文件
    log_file_path = os.path.join(DIR_PATH, "unblockgw.log")
    formatter = logging.Formatter(
        "%(asctime)s ~ %(levelname)-8s - "
        "%(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logfile = logging.handlers.RotatingFileHandler(
        log_file_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=1
    )
    logfile.setFormatter(formatter)
    logfile.setLevel(logging.DEBUG)
    root_logger.addHandler(logfile)

    # 输出日志到控制台
    formatter = logging.Formatter("%(message)s")
    # stderr logger
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    elogger.addHandler(console)
    # stdout logger
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.setLevel(logging.INFO)
    ologger.addHandler(console)

    # 设置 requests 和 urllib3 日志级别
    requests_logger = logging.getLogger("requests")
    requests_logger.setLevel(logging.WARNING)
    urllib3_logger = logging.getLogger('urllib3')
    urllib3_logger.setLevel(logging.WARNING)


if __name__ == '__main__':
    try:
        init_logging()
        main()
    except Exception as error:
        elogger.exception(error)
