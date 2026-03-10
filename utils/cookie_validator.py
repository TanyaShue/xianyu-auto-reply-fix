"""
Cookie有效性验证模块

通过调用闲鱼API检查Cookie是否有效
"""
import asyncio
import tempfile
import os
import time
from typing import Dict, Optional
from loguru import logger
import aiohttp


async def check_cookie_validity(cookie_value: str, cookie_id: str = "unknown") -> Dict:
    """
    检查Cookie有效性

    Args:
        cookie_value: Cookie字符串
        cookie_id: Cookie ID（用于日志标识）

    Returns:
        Dict: {
            'valid': bool,  # Cookie是否有效
            'user_info': dict,  # 用户信息（如果有效）
            'error': str,  # 错误信息（如果无效）
            'check_time': int  # 检查时间戳
        }
    """
    result = {
        'valid': False,
        'user_info': None,
        'error': None,
        'check_time': int(time.time())
    }

    if not cookie_value:
        result['error'] = "Cookie值为空"
        return result

    try:
        # 方法1：尝试获取用户信息来验证Cookie有效性
        user_info_result = await _check_user_info(cookie_value, cookie_id)

        if user_info_result['success']:
            result['valid'] = True
            result['user_info'] = user_info_result.get('user_info')
            logger.info(f"【{cookie_id}】Cookie验证通过: 用户信息获取成功")
            return result

        # 方法2：尝试图片上传API验证（备用方法）
        upload_result = await _check_image_upload(cookie_value, cookie_id)

        if upload_result['success']:
            result['valid'] = True
            logger.info(f"【{cookie_id}】Cookie验证通过: 图片上传API可用")
            return result

        # 两种方法都失败
        result['error'] = user_info_result.get('error') or upload_result.get('error') or "Cookie验证失败"
        logger.warning(f"【{cookie_id}】Cookie验证失败: {result['error']}")

    except Exception as e:
        error_msg = str(e)[:100]
        result['error'] = f"验证过程异常: {error_msg}"
        logger.error(f"【{cookie_id}】Cookie验证异常: {error_msg}")

    return result


async def _check_user_info(cookie_value: str, cookie_id: str) -> Dict:
    """
    通过获取用户信息API验证Cookie有效性

    Args:
        cookie_value: Cookie字符串
        cookie_id: Cookie ID

    Returns:
        Dict: {'success': bool, 'user_info': dict, 'error': str}
    """
    result = {
        'success': False,
        'user_info': None,
        'error': None
    }

    try:
        # 使用闲鱼用户信息API
        url = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsdk.searchsimilaritems/1.0/"

        headers = {
            'accept': 'application/json',
            'accept-language': 'zh-CN,zh;q=0.9',
            'content-type': 'application/x-www-form-urlencoded',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
            'referer': 'https://www.goofish.com/',
            'origin': 'https://www.goofish.com',
            'cookie': cookie_value
        }

        # 构造一个简单的请求参数
        timestamp = str(int(time.time() * 1000))

        # 从cookie中提取_m_h5_tk
        cookies_dict = {}
        for item in cookie_value.split(';'):
            if '=' in item:
                k, v = item.strip().split('=', 1)
                cookies_dict[k] = v

        m_h5_tk = cookies_dict.get('_m_h5_tk', '')
        token = m_h5_tk.split('_')[0] if m_h5_tk else ''

        # 尝试使用更简单的API：获取用户发布商品数量
        # 或者使用mtop.taobao.idlehome.home.init接口
        api_url = "https://h5api.m.goofish.com/h5/mtop.taobao.idlehome.home.init/1.0/"

        params = {
            'jsv': '2.7.2',
            'appKey': '34839810',
            't': timestamp,
            'sign': '',
            'v': '1.0',
            'type': 'originaljson',
            'dataType': 'json',
            'timeout': '20000',
            'api': 'mtop.taobao.idlehome.home.init',
        }

        data = {
            'data': '{}',
        }

        # 生成签名（简化版本，可能需要完整签名）
        from utils.xianyu_utils import generate_sign
        try:
            sign = generate_sign(timestamp, token, '{}')
            params['sign'] = sign
        except Exception:
            # 如果签名生成失败，尝试不带签名
            pass

        timeout = aiohttp.ClientTimeout(total=15)
        connector = aiohttp.TCPConnector(limit=10)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            async with session.post(api_url, params=params, data=data, headers=headers) as response:
                if response.status == 200:
                    try:
                        json_response = await response.json()

                        # 检查响应
                        ret = json_response.get('ret', [])

                        if ret and len(ret) > 0:
                            ret_str = str(ret[0]) if ret else ''

                            # 检查是否是成功响应或业务错误（说明Cookie有效）
                            if 'SUCCESS' in ret_str.upper() or 'FAIL_SYS_TOKEN_EMPTY' in ret_str:
                                result['success'] = True
                                result['user_info'] = {
                                    'response_type': 'api_response',
                                    'status': response.status
                                }
                                return result

                            # 检查是否是Token错误（Cookie无效）
                            if 'TOKEN_EXOIRED' in ret_str.upper() or 'SESSION_EXPIRED' in ret_str.upper():
                                result['error'] = f"Token/Session过期: {ret_str[:50]}"
                                return result

                            # 其他响应，可能Cookie有效
                            if 'FAIL_SYS' not in ret_str:
                                result['success'] = True
                                result['user_info'] = {
                                    'response_type': 'api_response',
                                    'status': response.status
                                }
                                return result

                        # 如果没有明确的错误，认为可能有效
                        result['success'] = True
                        result['user_info'] = {
                            'response_type': 'unknown',
                            'status': response.status
                        }

                    except Exception as json_e:
                        # JSON解析失败，可能返回了HTML页面
                        text = await response.text()
                        if '登录' in text or 'login' in text.lower():
                            result['error'] = "需要登录"
                        else:
                            # 无法确定，尝试备用方法
                            result['error'] = f"响应解析失败: {str(json_e)[:50]}"
                else:
                    result['error'] = f"HTTP错误: {response.status}"

    except asyncio.TimeoutError:
        result['error'] = "请求超时"
    except aiohttp.ClientError as e:
        result['error'] = f"网络错误: {str(e)[:50]}"
    except Exception as e:
        result['error'] = f"检查异常: {str(e)[:50]}"

    return result


async def _check_image_upload(cookie_value: str, cookie_id: str) -> Dict:
    """
    通过图片上传API验证Cookie有效性（备用方法）

    Args:
        cookie_value: Cookie字符串
        cookie_id: Cookie ID

    Returns:
        Dict: {'success': bool, 'error': str}
    """
    result = {
        'success': False,
        'error': None
    }

    try:
        # 创建测试图片
        from PIL import Image

        temp_dir = tempfile.gettempdir()
        test_image_path = os.path.join(temp_dir, f'cookie_test_{cookie_id}.png')

        try:
            # 创建1x1像素的白色图片
            img = Image.new('RGB', (1, 1), color='white')
            img.save(test_image_path, 'PNG')

            # 创建图片上传实例
            from utils.image_uploader import ImageUploader
            uploader = ImageUploader(cookies_str=cookie_value)

            await uploader.create_session()

            try:
                # 尝试上传测试图片
                upload_result = await uploader.upload_image(test_image_path)

                if upload_result:
                    result['success'] = True
                else:
                    result['error'] = "图片上传失败"

            finally:
                await uploader.close_session()

        finally:
            # 清理测试图片
            if os.path.exists(test_image_path):
                try:
                    os.remove(test_image_path)
                except Exception:
                    pass

    except ImportError:
        result['error'] = "PIL库未安装，跳过图片上传验证"
    except Exception as e:
        error_str = str(e)[:50]
        if '登录' in error_str or 'login' in error_str.lower():
            result['error'] = "需要登录"
        else:
            result['error'] = f"图片上传验证异常: {error_str}"

    return result


async def batch_check_cookies(cookies: Dict[str, str]) -> Dict[str, Dict]:
    """
    批量检查多个Cookie的有效性

    Args:
        cookies: {cookie_id: cookie_value} 字典

    Returns:
        Dict[str, Dict]: {cookie_id: check_result}
    """
    results = {}

    # 并发检查，但限制并发数
    semaphore = asyncio.Semaphore(3)  # 最多3个并发

    async def check_with_semaphore(cookie_id: str, cookie_value: str):
        async with semaphore:
            result = await check_cookie_validity(cookie_value, cookie_id)
            return cookie_id, result

    tasks = [
        check_with_semaphore(cookie_id, cookie_value)
        for cookie_id, cookie_value in cookies.items()
    ]

    if tasks:
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for item in results_list:
            if isinstance(item, Exception):
                logger.error(f"批量检查Cookie异常: {item}")
            elif isinstance(item, tuple):
                cookie_id, result = item
                results[cookie_id] = result

    return results