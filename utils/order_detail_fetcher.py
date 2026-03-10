"""
闲鱼订单详情获取工具
基于Playwright实现订单详情页面访问和数据提取
"""

import asyncio
import time
import sys
import os
from typing import Optional, Dict, Any
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from loguru import logger
import re
import json
from threading import Lock
from collections import defaultdict

# 修复Docker环境中的asyncio事件循环策略问题
if sys.platform.startswith('linux') or os.getenv('DOCKER_ENV'):
    try:
        # 在Linux/Docker环境中设置事件循环策略
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
    except Exception as e:
        logger.warning(f"设置事件循环策略失败: {e}")

# 确保在Docker环境中使用正确的事件循环
if os.getenv('DOCKER_ENV'):
    try:
        # 强制使用SelectorEventLoop（在Docker中更稳定）
        if hasattr(asyncio, 'SelectorEventLoop'):
            loop = asyncio.SelectorEventLoop()
            asyncio.set_event_loop(loop)
    except Exception as e:
        logger.warning(f"设置SelectorEventLoop失败: {e}")


class OrderDetailFetcher:
    """闲鱼订单详情获取器"""

    # 类级别的锁字典，为每个order_id维护一个锁
    _order_locks = defaultdict(lambda: asyncio.Lock())

    def __init__(self, cookie_string: str = None, headless: bool = True, cookie_id_for_log: str = "unknown"):
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.headless = headless  # 保存headless设置
        self.cookie_id_for_log = cookie_id_for_log or "unknown"

        # 请求头配置
        self.headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en,zh-CN;q=0.9,zh;q=0.8,ru;q=0.7",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1"
        }

        # Cookie配置 - 支持动态传入
        self.cookie = cookie_string

    async def init_browser(self, headless: bool = None):
        """初始化浏览器"""
        try:
            # 如果没有传入headless参数，使用实例的设置
            if headless is None:
                headless = self.headless

            logger.info(f"开始初始化浏览器，headless模式: {headless}")

            playwright = await async_playwright().start()

            # 启动浏览器（Docker环境优化）
            browser_args = [
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-accelerated-2d-canvas',
                '--no-first-run',
                '--no-zygote',
                '--disable-gpu',
                '--disable-background-timer-throttling',
                '--disable-backgrounding-occluded-windows',
                '--disable-renderer-backgrounding',
                '--disable-features=TranslateUI',
                '--disable-ipc-flooding-protection',
                '--disable-extensions',
                '--disable-default-apps',
                '--disable-sync',
                '--disable-translate',
                '--hide-scrollbars',
                '--mute-audio',
                '--no-default-browser-check',
                '--no-pings'
            ]

            # 移除--single-process参数，使用多进程模式提高稳定性
            # if os.getenv('DOCKER_ENV'):
            #     browser_args.append('--single-process')  # 注释掉，避免崩溃

            # 在Docker环境中添加额外参数
            if os.getenv('DOCKER_ENV'):
                browser_args.extend([
                    '--disable-background-networking',
                    '--disable-background-timer-throttling',
                    '--disable-client-side-phishing-detection',
                    '--disable-default-apps',
                    '--disable-hang-monitor',
                    '--disable-popup-blocking',
                    '--disable-prompt-on-repost',
                    '--disable-sync',
                    '--disable-web-resources',
                    '--metrics-recording-only',
                    '--no-first-run',
                    '--safebrowsing-disable-auto-update',
                    '--enable-automation',
                    '--password-store=basic',
                    '--use-mock-keychain',
                    # 添加内存优化和稳定性参数
                    '--memory-pressure-off',
                    '--max_old_space_size=512',
                    '--disable-ipc-flooding-protection',
                    '--disable-component-extensions-with-background-pages',
                    '--disable-features=TranslateUI,BlinkGenPropertyTrees',
                    '--disable-logging',
                    '--disable-permissions-api',
                    '--disable-notifications',
                    '--no-pings',
                    '--no-zygote'
                ])

            logger.info(f"启动浏览器，参数: {browser_args}")
            self.browser = await playwright.chromium.launch(
                headless=headless,
                args=browser_args
            )

            logger.info("浏览器启动成功，创建上下文...")

            # 创建浏览器上下文
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            )

            logger.info("浏览器上下文创建成功，设置HTTP头...")

            # 设置额外的HTTP头
            await self.context.set_extra_http_headers(self.headers)

            logger.info("创建页面...")

            # 创建页面
            self.page = await self.context.new_page()

            logger.info("页面创建成功，设置Cookie...")

            # 设置Cookie
            await self._set_cookies()

            # 等待一段时间确保浏览器完全初始化
            await asyncio.sleep(1)

            logger.info("浏览器初始化成功")
            return True
            
        except Exception as e:
            logger.error(f"浏览器初始化失败: {e}")
            return False

    async def _set_cookies(self):
        """设置Cookie"""
        try:
            # 解析Cookie字符串
            cookies = []
            for cookie_pair in self.cookie.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    cookies.append({
                        'name': name.strip(),
                        'value': value.strip(),
                        'domain': '.goofish.com',
                        'path': '/'
                    })
            
            # 添加Cookie到上下文
            await self.context.add_cookies(cookies)
            logger.info(f"已设置 {len(cookies)} 个Cookie")
            
        except Exception as e:
            logger.error(f"设置Cookie失败: {e}")

    async def fetch_order_detail(self, order_id: str, timeout: int = 30, force_refresh: bool = False) -> Optional[Dict[str, Any]]:
        """
        获取订单详情（带锁机制和数据库缓存）

        Args:
            order_id: 订单ID
            timeout: 超时时间（秒）
            force_refresh: 是否强制刷新（跳过缓存直接从闲鱼获取）

        Returns:
            包含订单详情的字典，失败时返回None
        """
        # 获取该订单ID的锁
        order_lock = self._order_locks[order_id]

        async with order_lock:
            logger.info(f"🔒 获取订单 {order_id} 的锁，开始处理...")

            try:
                # 如果不是强制刷新，先查询数据库缓存
                if not force_refresh:
                    from db_manager import db_manager
                    existing_order = db_manager.get_order_by_id(order_id)

                    if existing_order:
                        # 检查金额字段是否有效（不为空且不为0）
                        amount = existing_order.get('amount', '')
                        amount_valid = False

                        if amount:
                            # 移除可能的货币符号和空格，检查是否为有效数字
                            amount_clean = str(amount).replace('¥', '').replace('￥', '').replace('$', '').strip()
                            try:
                                amount_value = float(amount_clean)
                                amount_valid = amount_value > 0
                            except (ValueError, TypeError):
                                amount_valid = False

                        if amount_valid:
                            logger.info(f"📋 订单 {order_id} 已存在于数据库中且金额有效({amount})，直接返回缓存数据")
                            print(f"✅ 订单 {order_id} 使用缓存数据，跳过浏览器获取")

                            # 构建返回格式，与浏览器获取的格式保持一致
                            result = {
                                'order_id': existing_order['order_id'],
                                'url': f"https://www.goofish.com/order-detail?orderId={order_id}&role=seller",
                                'title': f"订单详情 - {order_id}",
                                'sku_info': {
                                    'spec_name': existing_order.get('spec_name', ''),
                                    'spec_value': existing_order.get('spec_value', ''),
                                    'spec_name_2': existing_order.get('spec_name_2', ''),
                                    'spec_value_2': existing_order.get('spec_value_2', ''),
                                    'quantity': existing_order.get('quantity', ''),
                                    'amount': existing_order.get('amount', '')
                                },
                                'spec_name': existing_order.get('spec_name', ''),
                                'spec_value': existing_order.get('spec_value', ''),
                                'spec_name_2': existing_order.get('spec_name_2', ''),
                                'spec_value_2': existing_order.get('spec_value_2', ''),
                                'quantity': existing_order.get('quantity', ''),
                                'amount': existing_order.get('amount', ''),
                                'timestamp': time.time(),
                                'from_cache': True  # 标记数据来源
                            }
                            return result
                        else:
                            logger.info(f"📋 订单 {order_id} 存在于数据库中但金额无效({amount})，需要重新获取")
                            print(f"⚠️ 订单 {order_id} 金额无效，重新获取详情...")
                else:
                    logger.info(f"🔄 订单 {order_id} 强制刷新模式，跳过缓存检查")

                # 只有在数据库中没有有效数据时才初始化浏览器
                logger.info(f"🌐 订单 {order_id} 需要浏览器获取，开始初始化浏览器...")
                print(f"🔍 订单 {order_id} 开始浏览器获取详情...")

                # 确保浏览器准备就绪
                if not await self._ensure_browser_ready():
                    logger.error("浏览器初始化失败，无法获取订单详情")
                    return None

                # 构建订单详情URL
                url = f"https://www.goofish.com/order-detail?orderId={order_id}&role=seller"
                logger.info(f"开始访问订单详情页面: {url}")

                # 访问页面（带重试机制）
                max_retries = 2
                response = None

                for retry in range(max_retries + 1):
                    try:
                        response = await self.page.goto(url, wait_until='networkidle', timeout=timeout * 1000)

                        if response and response.status == 200:
                            break
                        else:
                            logger.warning(f"页面访问失败，状态码: {response.status if response else 'None'}，重试 {retry + 1}/{max_retries + 1}")

                    except Exception as e:
                        logger.warning(f"页面访问异常: {e}，重试 {retry + 1}/{max_retries + 1}")

                        # 如果是浏览器连接问题，尝试重新初始化
                        if "Target page, context or browser has been closed" in str(e):
                            logger.info("检测到浏览器连接断开，尝试重新初始化...")
                            if await self._ensure_browser_ready():
                                logger.info("浏览器重新初始化成功，继续重试...")
                                continue
                            else:
                                logger.error("浏览器重新初始化失败")
                                return None

                        if retry == max_retries:
                            logger.error(f"页面访问最终失败: {e}")
                            return None

                        await asyncio.sleep(1)  # 重试前等待1秒

                if not response or response.status != 200:
                    logger.error(f"页面访问最终失败，状态码: {response.status if response else 'None'}")
                    return None

                logger.info("页面加载成功，等待内容渲染...")

                # 等待页面完全加载
                try:
                    await self.page.wait_for_load_state('networkidle')
                except Exception as e:
                    logger.warning(f"等待页面加载状态失败: {e}")
                    # 继续执行，不中断流程

                # 额外等待确保动态内容加载完成
                await asyncio.sleep(3)

                # 检查页面状态
                page_status = await self._check_page_status(order_id)
                if not page_status['is_valid']:
                    logger.warning(f"页面状态异常: {page_status}")
                    if page_status['needs_login']:
                        logger.error("检测到需要登录，Cookie可能已失效")
                        # 返回特殊错误标记，通知上层需要重新登录
                        return {
                            'order_id': order_id,
                            'error': 'NEED_LOGIN',
                            'message': 'Cookie可能已失效，需要重新登录',
                            'page_url': page_status['current_url'],
                            'page_title': page_status['page_title'],
                            'url': url,
                            'title': page_status['page_title'],
                            'sku_info': {},
                            'spec_name': '',
                            'spec_value': '',
                            'spec_name_2': '',
                            'spec_value_2': '',
                            'quantity': '',
                            'amount': '',
                            'order_status': 'unknown',
                            'timestamp': time.time(),
                            'from_cache': False
                        }

                # 获取并解析SKU信息
                sku_info = await self._get_sku_content()

                # 获取订单状态
                order_status = await self._get_order_status()
                logger.info(f"订单 {order_id} 状态: {order_status}")

                # 解析失败时，刷新页面后重试一次，降低偶发结构变化/异步渲染导致的漏解析概率
                if not self._is_order_detail_parse_success(sku_info, order_status):
                    self._log_order_detail_parse_event(
                        event_name="ORDER_DETAIL_PARSE_ALERT",
                        order_id=order_id,
                        url=url,
                        attempt="first",
                        sku_info=sku_info,
                        order_status=order_status,
                        level="warning"
                    )
                    logger.warning(
                        f"订单 {order_id} 首次解析结果不完整，准备刷新页面重试: "
                        f"sku_info={sku_info}, order_status={order_status}"
                    )
                    try:
                        await self.page.reload(wait_until='networkidle', timeout=timeout * 1000)
                        await asyncio.sleep(2)
                        retry_sku_info = await self._get_sku_content()
                        retry_order_status = await self._get_order_status()
                        logger.info(
                            f"订单 {order_id} 重试解析结果: sku_info={retry_sku_info}, "
                            f"order_status={retry_order_status}"
                        )

                        if self._is_order_detail_parse_success(retry_sku_info, retry_order_status):
                            sku_info = retry_sku_info
                            order_status = retry_order_status
                            logger.info(f"订单 {order_id} 刷新重试后解析成功")
                            self._log_order_detail_parse_event(
                                event_name="ORDER_DETAIL_PARSE_RECOVERED",
                                order_id=order_id,
                                url=url,
                                attempt="retry",
                                sku_info=sku_info,
                                order_status=order_status,
                                level="info"
                            )
                        else:
                            logger.warning(f"订单 {order_id} 刷新重试后仍未解析到完整详情")
                            self._log_order_detail_parse_event(
                                event_name="ORDER_DETAIL_PARSE_ALERT",
                                order_id=order_id,
                                url=url,
                                attempt="retry_final",
                                sku_info=retry_sku_info,
                                order_status=retry_order_status,
                                level="warning"
                            )
                    except Exception as retry_e:
                        logger.warning(f"订单 {order_id} 刷新重试解析异常: {retry_e}")
                        self._log_order_detail_parse_event(
                            event_name="ORDER_DETAIL_PARSE_ALERT",
                            order_id=order_id,
                            url=url,
                            attempt="retry_exception",
                            sku_info=sku_info,
                            order_status=order_status,
                            level="warning",
                            error=str(retry_e)
                        )

                # 获取页面标题
                try:
                    title = await self.page.title()
                except Exception as e:
                    logger.warning(f"获取页面标题失败: {e}")
                    title = f"订单详情 - {order_id}"

                result = {
                    'order_id': order_id,
                    'url': url,
                    'title': title,
                    'sku_info': sku_info,  # 包含解析后的规格信息
                    'spec_name': sku_info.get('spec_name', '') if sku_info else '',
                    'spec_value': sku_info.get('spec_value', '') if sku_info else '',
                    'spec_name_2': sku_info.get('spec_name_2', '') if sku_info else '',  # 规格2名称
                    'spec_value_2': sku_info.get('spec_value_2', '') if sku_info else '',  # 规格2值
                    'quantity': sku_info.get('quantity', '') if sku_info else '',  # 数量
                    'amount': sku_info.get('amount', '') if sku_info else '',      # 金额
                    'order_status': order_status,  # 订单状态
                    'timestamp': time.time(),
                    'from_cache': False  # 标记数据来源
                }

                logger.info(f"订单详情获取成功: {order_id}")
                if sku_info:
                    logger.info(f"规格信息 - 名称: {result['spec_name']}, 值: {result['spec_value']}")
                    logger.info(f"数量: {result['quantity']}, 金额: {result['amount']}")
                return result

            except Exception as e:
                logger.error(f"获取订单详情失败: {e}")
                return None

    def _parse_sku_content(self, sku_content: str) -> Dict[str, str]:
        """
        解析SKU内容，根据冒号分割规格名称和规格值
        支持双规格格式：例如 "版本选择:mac 版 - 单文件;远程:自行安装"

        Args:
            sku_content: 原始SKU内容字符串

        Returns:
            包含规格名称和规格值的字典，如果解析失败则返回空字典
            对于双规格，会额外包含 spec_name_2 和 spec_value_2
        """
        try:
            if not sku_content or ':' not in sku_content:
                logger.warning(f"SKU内容格式无效或不包含冒号: {sku_content}")
                return {}

            # 检查是否包含双规格（通过分号分隔，且分号后有冒号）
            # 格式如：版本选择:mac 版 - 单文件;远程:自行安装
            if ';' in sku_content:
                # 查找分号位置，检查分号后面是否有冒号（表示有第二个规格）
                semicolon_idx = sku_content.find(';')
                second_part = sku_content[semicolon_idx + 1:].strip()

                if ':' in second_part:
                    # 这是双规格格式
                    first_part = sku_content[:semicolon_idx].strip()

                    # 解析第一个规格
                    first_spec_parts = first_part.split(':', 1)
                    if len(first_spec_parts) == 2:
                        spec_name = first_spec_parts[0].strip()
                        spec_value = first_spec_parts[1].strip()
                    else:
                        logger.warning(f"第一个规格解析失败: {first_part}")
                        spec_name = ''
                        spec_value = first_part

                    # 解析第二个规格
                    second_spec_parts = second_part.split(':', 1)
                    spec_name_2 = second_spec_parts[0].strip()
                    spec_value_2 = second_spec_parts[1].strip() if len(second_spec_parts) > 1 else ''

                    result = {
                        'spec_name': spec_name,
                        'spec_value': spec_value
                    }

                    if spec_name_2 and spec_value_2:
                        result['spec_name_2'] = spec_name_2
                        result['spec_value_2'] = spec_value_2
                        logger.info(f"双规格解析成功 - 规格1: {spec_name}:{spec_value}, 规格2: {spec_name_2}:{spec_value_2}")
                    else:
                        logger.info(f"SKU解析成功（单规格）- 规格名称: {spec_name}, 规格值: {spec_value}")

                    return result

            # 单规格处理（原有逻辑）
            parts = sku_content.split(':', 1)  # 只分割第一个冒号

            if len(parts) == 2:
                spec_name = parts[0].strip()
                spec_value = parts[1].strip()

                if spec_name and spec_value:
                    result = {
                        'spec_name': spec_name,
                        'spec_value': spec_value
                    }
                    logger.info(f"SKU解析成功 - 规格名称: {spec_name}, 规格值: {spec_value}")
                    return result
                else:
                    logger.warning(f"SKU解析失败，规格名称或值为空: 名称='{spec_name}', 值='{spec_value}'")
                    return {}
            else:
                logger.warning(f"SKU内容分割失败: {sku_content}")
                return {}

        except Exception as e:
            logger.error(f"解析SKU内容异常: {e}")
            return {}

    def _normalize_amount_text(self, amount_text: str) -> Optional[str]:
        """标准化金额文本，返回纯数字字符串（如 29.90）"""
        try:
            if amount_text is None:
                return None
            text = str(amount_text).strip()
            if not text:
                return None

            # 优先提取货币格式
            money_match = re.search(r'[¥￥$]\s*([0-9]+(?:\.[0-9]{1,2})?)', text)
            if money_match:
                return money_match.group(1)

            # 兜底提取纯数字
            number_match = re.search(r'([0-9]+(?:\.[0-9]{1,2})?)', text)
            if number_match:
                return number_match.group(1)

            return None
        except Exception:
            return None

    def _has_valid_amount(self, amount_text: Any) -> bool:
        """判断金额是否可解析为数字（0 也视为有效）"""
        normalized = self._normalize_amount_text(str(amount_text) if amount_text is not None else '')
        if normalized is None:
            return False
        try:
            float(normalized)
            return True
        except (ValueError, TypeError):
            return False

    def _extract_status_from_text(self, text: str) -> str:
        """从任意文本中提取订单状态"""
        if not text:
            return 'unknown'

        status_patterns = [
            ('交易成功', 'success'),
            ('交易关闭', 'closed'),
            ('已关闭', 'closed'),
            ('待付款', 'pending_payment'),
            ('待发货', 'pending_delivery'),
            ('已发货', 'shipped'),
            ('待收货', 'shipped'),
            ('退款中', 'refunding'),
            ('退款成功', 'refunded'),
        ]
        for pattern, status in status_patterns:
            if pattern in text:
                return status
        return 'unknown'

    async def _get_page_text(self) -> str:
        """获取页面可读文本，失败时返回空字符串"""
        try:
            return (await self.page.inner_text('body')).strip()
        except Exception:
            try:
                html_content = await self.page.content()
                return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', html_content)).strip()
            except Exception:
                return ''

    def _extract_sku_from_text(self, text: str) -> Dict[str, str]:
        """从页面纯文本中兜底提取金额/规格/数量"""
        result: Dict[str, str] = {}
        if not text:
            return result

        lines = [line.strip() for line in text.splitlines() if line and line.strip()]

        # 优先从金额关键词行提取金额
        amount_keywords = ['实付款', '订单金额', '实收', '合计', '总价', '应付']
        for line in lines:
            if any(keyword in line for keyword in amount_keywords):
                normalized_amount = self._normalize_amount_text(line)
                if normalized_amount:
                    result['amount'] = normalized_amount
                    break

        # 兜底：从全文提取货币数字
        if 'amount' not in result:
            normalized_amount = self._normalize_amount_text(text)
            if normalized_amount:
                result['amount'] = normalized_amount

        # 数量提取
        quantity_patterns = [
            r'数量\s*[:：]?\s*x?\s*(\d+)',
            r'\bx\s*(\d{1,3})\b',
        ]
        for pattern in quantity_patterns:
            quantity_match = re.search(pattern, text, re.IGNORECASE)
            if quantity_match:
                result['quantity'] = quantity_match.group(1)
                break

        # 规格提取：过滤明显非规格行
        spec_candidates = []
        ignore_tokens = [
            'http://', 'https://', 'fleamarket://', '订单', '买家', '卖家', '地址',
            '手机', '电话', '时间', '发货', '付款', '交易', '退款', '去发货', '修改价格',
            '等待你发货', '等待买家', '已发货', '待收货', '待发货',
            # 新增：过滤备案信息
            '统一社会信用代码', '增值电信', '许可证', '备案', 'ICP', '营业执照',
            '广播电视', '演出许可', '集邮', 'APP备案', '网络食品', '食品经营'
        ]

        for line in lines:
            normalized_line = line.replace('：', ':')
            if ':' not in normalized_line:
                continue
            if any(token in normalized_line for token in ignore_tokens):
                continue

            left, right = normalized_line.split(':', 1)
            left = left.strip()
            right = right.strip()
            if not left or not right:
                continue
            if len(left) > 16:
                continue

            parsed = self._parse_sku_content(f"{left}:{right}")
            if parsed:
                spec_candidates.append(parsed)

        if spec_candidates:
            primary = spec_candidates[0]
            if primary.get('spec_name') and primary.get('spec_value'):
                result['spec_name'] = primary['spec_name']
                result['spec_value'] = primary['spec_value']
            if primary.get('spec_name_2') and primary.get('spec_value_2'):
                result['spec_name_2'] = primary['spec_name_2']
                result['spec_value_2'] = primary['spec_value_2']

            if len(spec_candidates) > 1 and 'spec_name_2' not in result:
                second = spec_candidates[1]
                if second.get('spec_name') and second.get('spec_value'):
                    result['spec_name_2'] = second['spec_name']
                    result['spec_value_2'] = second['spec_value']

        return result

    def _is_order_detail_parse_success(self, sku_info: Optional[Dict[str, str]], order_status: str) -> bool:
        """判定订单详情解析是否成功（金额/规格/状态任一有效即可）"""
        info = sku_info or {}
        has_valid_amount = self._has_valid_amount(info.get('amount'))
        has_valid_spec = bool(info.get('spec_name') and info.get('spec_value'))
        has_valid_status = bool(order_status and order_status != 'unknown')
        return has_valid_amount or has_valid_spec or has_valid_status

    def _build_parse_field_flags(self, sku_info: Optional[Dict[str, str]], order_status: str) -> Dict[str, Any]:
        """构建解析字段完整性标记，便于统一告警日志检索。"""
        info = sku_info or {}
        return {
            'has_amount': self._has_valid_amount(info.get('amount')),
            'has_spec': bool(info.get('spec_name') and info.get('spec_value')),
            'has_status': bool(order_status and order_status != 'unknown'),
            'amount': info.get('amount', ''),
            'spec_name': info.get('spec_name', ''),
            'spec_value': info.get('spec_value', ''),
            'quantity': info.get('quantity', ''),
            'order_status': order_status or ''
        }

    def _log_order_detail_parse_event(
        self,
        event_name: str,
        order_id: str,
        url: str,
        attempt: str,
        sku_info: Optional[Dict[str, str]],
        order_status: str,
        level: str = "warning",
        error: str = None
    ) -> None:
        """输出结构化的订单详情解析告警/恢复日志。"""
        try:
            field_flags = self._build_parse_field_flags(sku_info, order_status)
            payload = {
                'event': event_name,
                'cookie_id': self.cookie_id_for_log,
                'order_id': order_id,
                'attempt': attempt,
                'url': url,
                'field_flags': field_flags
            }
            if error:
                payload['error'] = error

            log_msg = f"{event_name} {json.dumps(payload, ensure_ascii=False, sort_keys=True)}"
            if level == "info":
                logger.info(log_msg)
            else:
                logger.warning(log_msg)
        except Exception as log_error:
            logger.warning(f"订单解析事件日志输出失败: {log_error}")

    async def _get_order_status(self) -> str:
        """
        从订单详情页面获取订单状态

        Returns:
            订单状态字符串，可能的值:
            - 'success': 交易成功
            - 'closed': 交易关闭
            - 'pending_payment': 待付款
            - 'pending_delivery': 待发货
            - 'shipped': 已发货/待收货
            - 'refunding': 退款中
            - 'unknown': 未知状态
        """
        try:
            if not await self._check_browser_status():
                logger.error("浏览器状态异常，无法获取订单状态")
                return 'unknown'

            # 尝试多种选择器获取订单状态
            status_selectors = [
                '.orderStatusText--F6eoVcHD',  # 常见的订单状态选择器
                '.order-status',
                '.status-text',
                '[class*="orderStatus"]',
                '[class*="StatusText"]',
                '[class*="status"]',
            ]

            status_text = ''
            for selector in status_selectors:
                try:
                    element = await self.page.query_selector(selector)
                    if element:
                        text = await element.text_content()
                        if text:
                            status_text = text.strip()
                            logger.info(f"通过选择器 {selector} 获取到订单状态: {status_text}")
                            break
                except Exception as e:
                    logger.debug(f"选择器 {selector} 获取失败: {e}")
                    continue

            parsed_from_selector = 'unknown'

            # 先解析选择器结果
            if status_text:
                parsed_from_selector = self._extract_status_from_text(status_text)
                if parsed_from_selector != 'unknown':
                    logger.info(f"订单状态解析: {status_text} -> {parsed_from_selector}")
                    return parsed_from_selector
                logger.warning(f"未知的订单状态文本: {status_text}")

            # 如果选择器失败或未识别，尝试从页面文本中提取
            if not status_text or parsed_from_selector == 'unknown':
                body_text = await self._get_page_text()
                parsed = self._extract_status_from_text(body_text)
                if parsed != 'unknown':
                    logger.info(f"从页面文本中检测到订单状态 -> {parsed}")
                    return parsed

            logger.warning("无法获取订单状态")
            return 'unknown'

        except Exception as e:
            logger.error(f"获取订单状态异常: {e}")
            return 'unknown'

    async def _get_sku_content(self) -> Optional[Dict[str, str]]:
        """获取并解析SKU内容，包括规格、数量和金额，支持双规格"""
        try:
            # 检查浏览器状态
            if not await self._check_browser_status():
                logger.error("浏览器状态异常，无法获取SKU内容")
                return {}

            result: Dict[str, str] = {}

            # 输出页面URL和标题用于调试
            try:
                current_url = self.page.url
                page_title = await self.page.title()
                logger.info(f"📍 当前页面URL: {current_url}")
                logger.info(f"📄 页面标题: {page_title}")
            except Exception as e:
                logger.warning(f"获取页面URL/标题失败: {e}")

            # 输出页面内容摘要（前500字符）用于调试
            try:
                body_text = await self._get_page_text()
                if body_text:
                    logger.debug(f"📝 页面内容摘要: {body_text[:500]}...")
            except Exception as e:
                logger.warning(f"获取页面内容摘要失败: {e}")

            # 获取规格元素（主通道）
            sku_selector = '.sku--u_ddZval'
            sku_elements = await self.page.query_selector_all(sku_selector)
            logger.info(f"找到 {len(sku_elements)} 个 sku--u_ddZval 元素")

            # 获取金额（多选择器兜底）
            amount_selectors = [
                '.boldNum--JgEOXfA3',
                '[class*="boldNum"]',
                '[class*="pay"] [class*="num"]',
                '[class*="amount"] [class*="num"]',
                '[class*="price"] [class*="num"]',
            ]
            for amount_selector in amount_selectors:
                try:
                    amount_element = await self.page.query_selector(amount_selector)
                    if not amount_element:
                        continue
                    amount_text = await amount_element.text_content()
                    normalized_amount = self._normalize_amount_text(amount_text or '')
                    if normalized_amount:
                        result['amount'] = normalized_amount
                        logger.info(f"通过选择器 {amount_selector} 找到金额: {normalized_amount}")
                        break
                except Exception as selector_e:
                    logger.debug(f"金额选择器 {amount_selector} 解析失败: {selector_e}")

            # 收集所有元素的内容
            all_contents = []
            for i, element in enumerate(sku_elements):
                content = await element.text_content()
                if content:
                    content = content.strip()
                    all_contents.append(content)
                    logger.info(f"元素 {i+1} 原始内容: {content}")

            # 分类：规格 vs 数量
            specs = []
            quantity_content = None

            for content in all_contents:
                if '数量' in content:
                    # 这是数量
                    quantity_content = content
                elif ':' in content:
                    # 这是规格（包含冒号的）
                    specs.append(content)
                else:
                    # 没有冒号也没有"数量"，可能是纯数字（如 x1）
                    if content.startswith('x') or content.isdigit():
                        quantity_content = content
                    else:
                        # 其他情况当作规格处理
                        specs.append(content)

            # 解析规格1（主通道）
            if len(specs) >= 1:
                parsed_spec = self._parse_sku_content(specs[0])
                if parsed_spec:
                    result['spec_name'] = parsed_spec['spec_name']
                    result['spec_value'] = parsed_spec['spec_value']

                    # 检查第一个规格是否已包含双规格（分号分隔的情况）
                    if 'spec_name_2' in parsed_spec and 'spec_value_2' in parsed_spec:
                        result['spec_name_2'] = parsed_spec['spec_name_2']
                        result['spec_value_2'] = parsed_spec['spec_value_2']

            # 解析规格2（如果存在且尚未从分号分隔中获取）
            if len(specs) >= 2 and 'spec_name_2' not in result:
                parsed_spec2 = self._parse_sku_content(specs[1])
                if parsed_spec2:
                    result['spec_name_2'] = parsed_spec2['spec_name']
                    result['spec_value_2'] = parsed_spec2['spec_value']

            # 如果有更多规格，记录日志（目前只支持双规格）
            if len(specs) > 2:
                logger.warning(f"检测到 {len(specs)} 个规格，目前只支持双规格，多余的规格将被忽略")

            # 解析数量
            if quantity_content:
                logger.info(f"数量原始内容: {quantity_content}")

                if ':' in quantity_content:
                    quantity_value = quantity_content.split(':', 1)[1].strip()
                else:
                    quantity_value = quantity_content

                # 去掉数量值前面的 'x' 符号（如 "x2" -> "2"）
                if quantity_value.startswith('x'):
                    quantity_value = quantity_value[1:]

                result['quantity'] = quantity_value
                logger.info(f"提取到数量: {quantity_value}")

            # 如果核心字段缺失，使用页面文本兜底
            if (
                'amount' not in result
                or 'spec_name' not in result
                or 'spec_value' not in result
                or 'quantity' not in result
            ):
                page_text = await self._get_page_text()
                fallback_result = self._extract_sku_from_text(page_text)

                for key in ['amount', 'spec_name', 'spec_value', 'spec_name_2', 'spec_value_2', 'quantity']:
                    if key not in result and fallback_result.get(key):
                        result[key] = fallback_result[key]

                if fallback_result:
                    logger.info(f"SKU文本兜底解析结果: {fallback_result}")

            # 确保数量字段存在，如果不存在则设置为1
            if 'quantity' not in result:
                result['quantity'] = '1'
                logger.info("未获取到数量信息，默认设置为1")

            # 打印最终结果
            if result:
                logger.info(f"最终解析结果: {result}")
                return result
            else:
                logger.warning("未能解析到任何有效信息")
                # 即使没有其他信息，也要返回默认数量
                return {'quantity': '0'}

        except Exception as e:
            logger.error(f"获取SKU内容失败: {e}")
            return {}

    async def _check_page_status(self, order_id: str) -> Dict[str, Any]:
        """
        检查页面状态，检测是否正确加载、是否需要登录等

        Args:
            order_id: 订单ID，用于日志输出

        Returns:
            包含页面状态信息的字典：
            - is_valid: 页面是否有效
            - needs_login: 是否需要登录
            - current_url: 当前页面URL
            - page_title: 页面标题
            - error_message: 错误信息
        """
        result = {
            'is_valid': True,
            'needs_login': False,
            'current_url': '',
            'page_title': '',
            'error_message': ''
        }

        try:
            # 获取当前页面URL和标题
            current_url = self.page.url
            page_title = await self.page.title()

            result['current_url'] = current_url
            result['page_title'] = page_title

            # 检测是否被重定向到登录页面
            login_indicators = ['login', 'signin', 'verify', 'captcha', 'safe']
            if any(indicator in current_url.lower() for indicator in login_indicators):
                result['is_valid'] = False
                result['needs_login'] = True
                result['error_message'] = f'页面被重定向到登录/验证页面: {current_url}'
                logger.warning(f"订单 {order_id}: 检测到登录/验证页面重定向 - {current_url}")
                return result

            # 检测页面标题是否包含登录相关关键词
            login_title_indicators = ['登录', '验证', '安全校验', '请登录', '扫码登录']
            if any(indicator in page_title for indicator in login_title_indicators):
                result['is_valid'] = False
                result['needs_login'] = True
                result['error_message'] = f'页面标题包含登录相关内容: {page_title}'
                logger.warning(f"订单 {order_id}: 页面标题包含登录相关内容 - {page_title}")
                return result

            # 检测是否是正确的订单详情页面
            expected_url_patterns = ['order-detail', 'orderId', 'goofish.com']
            if not any(pattern in current_url for pattern in expected_url_patterns):
                result['is_valid'] = False
                result['error_message'] = f'页面URL不符合预期: {current_url}'
                logger.warning(f"订单 {order_id}: 页面URL不符合预期 - {current_url}")
                return result

            # 检测页面是否包含订单详情的关键元素
            try:
                # 尝试查找订单相关元素
                order_indicators = await self.page.query_selector_all('.sku--u_ddZval, [class*="order"], [class*="Order"]')
                if not order_indicators:
                    # 没有找到订单相关元素，可能页面结构异常或需要登录
                    logger.warning(f"订单 {order_id}: 页面未找到订单相关元素")
                    # 检查是否有登录按钮
                    login_button = await self.page.query_selector('[class*="login"], button:has-text("登录")')
                    if login_button:
                        result['is_valid'] = False
                        result['needs_login'] = True
                        result['error_message'] = '页面存在登录按钮，Cookie可能已失效'
                        logger.warning(f"订单 {order_id}: 检测到登录按钮，Cookie可能已失效")
            except Exception as e:
                logger.debug(f"检查订单元素时出错: {e}")

            return result

        except Exception as e:
            logger.error(f"检查页面状态失败: {e}")
            result['is_valid'] = False
            result['error_message'] = str(e)
            return result

    async def _check_browser_status(self) -> bool:
        """检查浏览器状态是否正常"""
        try:
            if not self.browser or not self.context or not self.page:
                logger.warning("浏览器组件不完整")
                return False

            # 检查浏览器是否已连接
            if self.browser.is_connected():
                # 尝试获取页面标题来验证页面是否可用
                await self.page.title()
                return True
            else:
                logger.warning("浏览器连接已断开")
                return False
        except Exception as e:
            logger.warning(f"浏览器状态检查失败: {e}")
            return False

    async def _ensure_browser_ready(self) -> bool:
        """确保浏览器准备就绪，如果不可用则重新初始化"""
        try:
            if await self._check_browser_status():
                return True

            logger.info("浏览器状态异常，尝试重新初始化...")

            # 先尝试关闭现有的浏览器实例
            await self._force_close_browser()

            # 重新初始化浏览器
            await self.init_browser()

            # 等待更长时间确保浏览器完全就绪
            await asyncio.sleep(2)

            # 再次检查状态
            if await self._check_browser_status():
                logger.info("浏览器重新初始化成功")
                return True
            else:
                logger.error("浏览器重新初始化失败")
                return False

        except Exception as e:
            logger.error(f"确保浏览器就绪失败: {e}")
            return False

    async def _force_close_browser(self):
        """强制关闭浏览器，忽略所有错误"""
        try:
            if self.page:
                try:
                    await self.page.close()
                except:
                    pass
                self.page = None

            if self.context:
                try:
                    await self.context.close()
                except:
                    pass
                self.context = None

            if self.browser:
                try:
                    await self.browser.close()
                except:
                    pass
                self.browser = None

        except Exception as e:
            logger.debug(f"强制关闭浏览器过程中的异常（可忽略）: {e}")

    async def close(self):
        """关闭浏览器"""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            logger.info("浏览器已关闭")
        except Exception as e:
            logger.error(f"关闭浏览器失败: {e}")
            # 如果正常关闭失败，尝试强制关闭
            await self._force_close_browser()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self.init_browser()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()


# 便捷函数
async def fetch_order_detail_simple(
    order_id: str,
    cookie_string: str = None,
    headless: bool = True,
    force_refresh: bool = False,
    cookie_id_for_log: str = "unknown"
) -> Optional[Dict[str, Any]]:
    """
    简单的订单详情获取函数（优化版：先检查数据库，再初始化浏览器）

    Args:
        order_id: 订单ID
        cookie_string: Cookie字符串，如果不提供则使用默认值
        headless: 是否无头模式
        force_refresh: 是否强制刷新（跳过缓存直接从闲鱼获取）
        cookie_id_for_log: 日志上下文中的账号ID，用于定位异常账号

    Returns:
        订单详情字典，包含以下字段：
        - order_id: 订单ID
        - url: 订单详情页面URL
        - title: 页面标题
        - sku_info: 完整的SKU信息字典
        - spec_name: 规格名称
        - spec_value: 规格值
        - quantity: 数量
        - amount: 金额
        - order_status: 订单状态
        - timestamp: 获取时间戳
        失败时返回None
    """
    # 如果不是强制刷新，先检查数据库中是否有有效数据
    if not force_refresh:
        try:
            from db_manager import db_manager
            existing_order = db_manager.get_order_by_id(order_id)

            if existing_order:
                # 检查金额字段是否有效
                amount = existing_order.get('amount', '')
                amount_valid = False

                if amount:
                    amount_clean = str(amount).replace('¥', '').replace('￥', '').replace('$', '').strip()
                    try:
                        amount_value = float(amount_clean)
                        amount_valid = amount_value > 0
                    except (ValueError, TypeError):
                        amount_valid = False

                if amount_valid:
                    logger.info(f"📋 订单 {order_id} 已存在于数据库中且金额有效({amount})，直接返回缓存数据")
                    print(f"✅ 订单 {order_id} 使用缓存数据，跳过浏览器获取")

                    # 构建返回格式
                    result = {
                        'order_id': existing_order['order_id'],
                        'url': f"https://www.goofish.com/order-detail?orderId={order_id}&role=seller",
                        'title': f"订单详情 - {order_id}",
                        'sku_info': {
                            'spec_name': existing_order.get('spec_name', ''),
                            'spec_value': existing_order.get('spec_value', ''),
                            'spec_name_2': existing_order.get('spec_name_2', ''),
                            'spec_value_2': existing_order.get('spec_value_2', ''),
                            'quantity': existing_order.get('quantity', ''),
                            'amount': existing_order.get('amount', '')
                        },
                        'spec_name': existing_order.get('spec_name', ''),
                        'spec_value': existing_order.get('spec_value', ''),
                        'spec_name_2': existing_order.get('spec_name_2', ''),
                        'spec_value_2': existing_order.get('spec_value_2', ''),
                        'quantity': existing_order.get('quantity', ''),
                        'amount': existing_order.get('amount', ''),
                        'order_status': existing_order.get('order_status', 'unknown'),  # 添加订单状态
                        'timestamp': time.time(),
                        'from_cache': True
                    }
                    return result
                else:
                    logger.info(f"📋 订单 {order_id} 存在于数据库中但金额无效({amount})，需要重新获取")
                    print(f"⚠️ 订单 {order_id} 金额无效，重新获取详情...")
        except Exception as e:
            logger.warning(f"检查数据库缓存失败: {e}")
    else:
        logger.info(f"🔄 订单 {order_id} 强制刷新，跳过缓存检查")
        print(f"🔄 订单 {order_id} 强制刷新模式...")

    # 数据库中没有有效数据，使用浏览器获取
    logger.info(f"🌐 订单 {order_id} 需要浏览器获取，开始初始化浏览器...")
    print(f"🔍 订单 {order_id} 开始浏览器获取详情...")

    fetcher = OrderDetailFetcher(cookie_string, headless, cookie_id_for_log=cookie_id_for_log)
    try:
        if await fetcher.init_browser(headless=headless):
            return await fetcher.fetch_order_detail(order_id, force_refresh=force_refresh)
    finally:
        await fetcher.close()
    return None


# 测试代码
if __name__ == "__main__":
    async def test():
        # 测试订单ID
        test_order_id = "2856024697612814489"
        
        print(f"🔍 开始获取订单详情: {test_order_id}")
        
        result = await fetch_order_detail_simple(test_order_id, headless=False)
        
        if result:
            print("✅ 订单详情获取成功:")
            print(f"📋 订单ID: {result['order_id']}")
            print(f"🌐 URL: {result['url']}")
            print(f"📄 页面标题: {result['title']}")
            print(f"🛍️ 规格名称: {result.get('spec_name', '未获取到')}")
            print(f"📝 规格值: {result.get('spec_value', '未获取到')}")
            print(f"🔢 数量: {result.get('quantity', '未获取到')}")
            print(f"💰 金额: {result.get('amount', '未获取到')}")
        else:
            print("❌ 订单详情获取失败")
    
    # 运行测试
    asyncio.run(test())
