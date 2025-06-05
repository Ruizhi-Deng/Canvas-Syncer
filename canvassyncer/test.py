import asyncio
import httpx

async def test_request():
    # 创建与你配置相同的异步客户端
    async with httpx.AsyncClient(
        timeout=30,  # 建议增大超时时间
        headers={
            "Authorization": "Bearer <REMOVED>",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        },
        follow_redirects=True,
        verify=False
    ) as client:
        try:
            # 测试对目标网站的简单GET请求
            resp = await client.get("https://oc.sjtu.edu.cn/api/v1/courses?page=1")
            print(f"状态码: {resp.status_code}")
            print(f"响应长度: {len(resp.text)}")
            print(f"响应: {resp.text}")
            return True
        except Exception as e:
            print(f"错误: {e.__class__.__name__}: {e}")
            return False

# 直接运行异步函数
result = asyncio.run(test_request())
print(f"请求测试结果: {'成功' if result else '失败'}")
