import asyncio
import json
import os

import aiofiles
import httpx
from tqdm import tqdm


class AsyncSemClient:
    def __init__(self, connectionCount, token, proxy):
        self.sem = asyncio.Semaphore(connectionCount)
        self.client = httpx.AsyncClient(
            timeout=10,
            headers={
                "Authorization": f"Bearer {token}",
                # "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            },
            proxy=proxy,
            # proxy=None,
            # transport=httpx.AsyncHTTPTransport(retries=3),
            follow_redirects=True,
        )

    async def downloadOne(self, src, dst):
        async with self.sem:
            async with self.client.stream("GET", src) as res:
                if res.status_code >= 400:
                    return self.failures.append(f"{src} => {dst}")
                num_bytes_downloaded = res.num_bytes_downloaded
                dst_temp = dst + ".temp"
                try:
                    async with aiofiles.open(dst_temp, "+wb") as f:
                        async for chunk in res.aiter_bytes():
                            await f.write(chunk)
                            self.tqdm.update(
                                res.num_bytes_downloaded - num_bytes_downloaded
                            )
                            num_bytes_downloaded = res.num_bytes_downloaded
                except Exception as e:
                    print(
                        f"\n{e.__class__.__name__}: Fail to download {src} => {dst_temp}."
                    )
                    self.failures.append(dst)
                    os.remove(dst_temp)
                    return
                # Rename the temp file to the final destination
                try:
                    os.rename(dst_temp, dst)
                except FileNotFoundError as e:
                    print(
                        "\nFileNotFoundError: Perhaps the file path is too long or invalid. Progress indicator will be inaccurate."
                    )
                    self.failures.append(dst)
                    # os.remove(dst_temp)
                    return
                except FileExistsError as e:
                    base, ext = os.path.splitext(dst)
                    new_name = base + "_online_ver" + ext
                    print(
                        f"\nFileExistsError: {dst} already exists. New file will be renamed to {new_name}."
                    )
                    if os.path.exists(new_name):
                        print(f"File {new_name} already exists. Update it.")
                        os.remove(new_name)
                        os.rename(dst_temp, new_name)
                    else:
                        os.rename(dst_temp, new_name)
                        
                except Exception as e:
                    print(f"\n{e.__class__.__name__}")
                    os.remove(dst_temp)
                    self.failures.append(dst)
                    return

    async def downloadMany(
        self, download_dir_root, download_list, download_size, course_code
    ):
        self.tqdm = tqdm(total=download_size, unit="B", unit_scale=True)
        self.failures = []
        tmp_list = []
        for courseID in download_list.keys():
            for file_name, file_info in download_list[courseID].items():
                full_file_path = (
                    os.path.join(
                        download_dir_root, f"{course_code[courseID]}{file_name}"
                    )
                    .replace("\\", "/")
                    .replace("//", "/")
                )
                tmp_list.append((file_info["url"], full_file_path))
        await asyncio.gather(
            *[asyncio.create_task(self.downloadOne(src, dst)) for src, dst in tmp_list]
        )
        self.tqdm.close()
        if self.failures:
            print(f"Fail to download these {len(self.failures)} file(s):")
            for text in self.failures:
                print("\t" + text)

    async def json(self, *args, **kwargs):
        retryTimes = 1
        checkError = bool(kwargs.pop("checkError", False))
        debugMode = bool(kwargs.pop("debug", False))
        while retryTimes <= 5:
            try:
                async with self.sem:
                    resp = await self.client.get(*args, **kwargs)
                text = resp.text
                if resp.status_code == 403 and "Rate Limit Exceeded" in text:
                    print("Rate limit exceeded. Waiting 5s before retrying...")
                    await asyncio.sleep(5)  # 等待 5 秒后重试
                    retryTimes += 1
                    continue
                try:
                    res = resp.json()
                except json.JSONDecodeError as e:
                    print(f"JSONDecodeError: {e}")
                    print(f"Response text: {text}")
                    raise e
                if checkError and isinstance(res, dict) and res.get("errors"):
                    errMsg = res["errors"][0].get("message", "unknown error.")
                    print(f"\nError: {errMsg}")
                    exit(1)
                return res

            except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
                retryTimes += 1
                waitTime = 5
                print(f"\nTimeout error: {e}. Retry #{retryTimes}, waiting for {waitTime} seconds...")
                print(f"Request URL: {args}")
                print(f"Request parameters: {kwargs}")
                await asyncio.sleep(waitTime)

            except Exception as e:
                retryTimes += 1
                if debugMode:
                    print(f"{e.__class__.__name__}. Retry. {retryTimes} times.")

    async def head(self, *args, **kwargs):
        async with self.sem:
            resp = await self.client.head(*args, **kwargs)
        return resp.headers

    async def aclose(self):
        await self.client.aclose()
