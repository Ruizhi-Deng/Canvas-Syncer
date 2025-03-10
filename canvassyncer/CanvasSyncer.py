import asyncio
import mimetypes
import os
import re
from datetime import datetime, timezone

from AsyncSemClient import AsyncSemClient

import pywintypes
import win32file
import win32con


PAGES_PER_TIME = 8
FIND_COURSE_RETRY_TIMES = 3
WINDOWS_PATH_MAX_LENGTH = 260
PATH_LENGTH_TOLERANCE = 10


class CanvasSyncer:
    def __init__(self, config):
        self.config = config
        self.client = AsyncSemClient(
            config["connection_count"], config["token"], config.get("proxy")
        )
        self.courseCode = {}
        self.baseUrl = self.config["canvasURL"] + "/api/v1"
        self.downloadDir = self.config["downloadDir"]

        self.folders = {}

        # FILE DICTIONARY: 2-stage dict
        # {courseID:{relative_file_path (to course root):{"name":<str> (localFiles Only), "url":<str> (not for localFiles), size:<int>, modified_date:<int>}}}
        self.localFiles = {}
        self.onlineFiles = {}
        self.laterFiles = {}
        self.newFiles = {}
        self.overSizedFiles = {}
        self.downloadList = {}
        self.downloadSize = 0
        self.totalFileCount = 0
        if not os.path.exists(self.downloadDir):
            os.mkdir(self.downloadDir)

    async def aclose(self):
        await self.client.aclose()

    async def dictFromPages(self, helperFunc, *args, **kwargs):
        res = {}
        page = 1
        endOfPage = False
        while not endOfPage:
            pageRes = await asyncio.gather(
                *[helperFunc(page + i, *args, **kwargs) for i in range(PAGES_PER_TIME)]
            )
            for item in pageRes:
                if not item:
                    endOfPage = True
                res.update(item)
            page += PAGES_PER_TIME
        return res

    def countFiles(self, filesDict):
        count = 0
        for courseID in filesDict:
            count += len(filesDict[courseID])
        return count

    async def getCourseID(self):
        coros = []
        if self.config.get("courseCodes"):
            coros.append(self.getCourseIdByCourseCode())
        if self.config.get("courseIDs"):
            coros.append(self.getCourseCodeByCourseID())
        await asyncio.gather(*coros)

    async def getCourseIdByCourseCode(self):
        lowerCourseCodes = []
        for courseCode in self.config["courseCodes"]:
            lowerCourseCodes.append(courseCode.replace(" ", "").lower())
        self.courseCode.update(
            await self.dictFromPages(
                self.getCourseIdByCourseCodeHelper, lowerCourseCodes
            )
        )
        if lowerCourseCodes:
            for courseCode in lowerCourseCodes:
                print(f"\t Cannot find course ID for course: {courseCode}")

    # def ifCourseCodeExists(self, code, lookUpList):
    #     for course in lookUpList:
    #         if course in code:
    #             return (True, course)
    #     return False

    def formatSJTUSyleCourseCode(self, courseCode):
        pattern = r"[a-z]+[0-9]+"
        match = re.findall(pattern, courseCode)
        if match:
            return match[0]
        else:
            return courseCode

    async def getCourseIdByCourseCodeHelper(self, page, lowerCourseCodes):
        res = {}
        url = f"{self.baseUrl}/courses?page={page}"
        courses = await self.client.json(
            url, checkError=True, debug=self.config["debug"]
        )
        if not courses:
            return res
        for course in courses:
            unformattedCourseCode = course.get("course_code", "").lower()
            courseCode = self.formatSJTUSyleCourseCode(unformattedCourseCode)
            if courseCode in lowerCourseCodes:
                lowerCourseCodes.remove(courseCode)
                courseCode = courseCode.upper()
                res[course["id"]] = courseCode
                print(
                    f"\t Get course ID: {course['id']} from course code: {courseCode}"
                )
            # else:
            # print(f"\t No course has this code: {course['course_code']}")
        return res

    async def getCourseCodeByCourseID(self):
        await asyncio.gather(
            *[
                asyncio.create_task(self.getCourseCodeByCourseIDHelper(courseID))
                for courseID in self.config["courseIDs"]
            ]
        )

    async def getCourseCodeByCourseIDHelper(self, courseID):
        url = f"{self.baseUrl}/courses/{courseID}"
        clientRes = await self.client.json(url, debug=self.config["debug"])
        if clientRes.get("course_code") is None:
            print(f"\t Cannot get course code from course ID: {courseID}")
            return
        self.courseCode[int(courseID)] = self.formatSJTUSyleCourseCode(
            clientRes["course_code"]
        )
        print(
            f"\t Get course code: {clientRes['course_code']} from course ID: {courseID}"
        )

    # TODO check validity of the func
    def pathTooLong(self, path, max_length=WINDOWS_PATH_MAX_LENGTH):
        return len(path) > max_length - len(self.downloadDir) - PATH_LENGTH_TOLERANCE

    # TODO check validity of the func
    def shorten_path(self, path, max_length=WINDOWS_PATH_MAX_LENGTH):
        if not self.pathTooLong(path, max_length):
            return path
        base_name = os.path.basename(path)
        parent_dir = os.path.dirname(path)
        truncated_parent = (
            parent_dir[: max_length - len(base_name) - PATH_LENGTH_TOLERANCE] + ".../"
        )
        return os.path.join(truncated_parent, base_name)

    # TODO fix the issue of too long path, need unify the path
    def scanLocalFiles(self, courseID, folders):
        localFiles = {}
        for folder in folders.values():
            if self.config["no_subfolder"]:
                path = os.path.join(self.downloadDir, folder[1:])
            else:
                path = os.path.join(
                    self.downloadDir, f"{self.courseCode[courseID]}{folder}"
                )
            if self.pathTooLong(path):
                print(f"Path too long: {path}")
                continue
            # path = self.shorten_path(path)
            if not os.path.exists(path):
                try:
                    os.makedirs(path)
                # except FileNotFoundError as e:
                # try:
                # path = self.shorten_path(path)
                # os.makedirs(path)
                # except Exception as e:
                # print(f"Error: {e}")
                except Exception as e:
                    print(f"Error: {e}")

            for f in os.listdir(path):
                if not os.path.isdir(os.path.join(path, f)):
                    full_file_path = (
                        os.path.join(path, f).replace("\\", "/").replace("//", "/")
                    )
                    relative_file_path = (
                        os.path.join(folder, f).replace("\\", "/").replace("//", "/")
                    )
                    localFiles[relative_file_path] = {
                        "name": f,
                        "size": os.path.getsize(full_file_path),
                        "modified_time": int(os.path.getmtime(full_file_path)),
                        "created_time": int(os.path.getctime(full_file_path)),
                        "is_modified": bool(
                            os.path.getmtime(full_file_path)
                            > os.path.getctime(full_file_path)
                        ),
                    }
        return localFiles

    async def getCourseFiles(self, courseID):
        self.folders[courseID] = await self.dictFromPages(
            self.getCourseFoldersWithIDHelper, courseID
        )
        self.onlineFiles[courseID] = await self.dictFromPages(
            self.getCourseFilesHelper, courseID, self.folders[courseID]
        )

    async def getCourseFoldersWithIDHelper(self, page, courseID):
        res = {}
        url = f"{self.baseUrl}/courses/{courseID}/folders?page={page}"
        retryTimes = 0
        while retryTimes < 5:
            try:
                folders = await self.client.json(url, debug=self.config["debug"])
                for folder in folders:
                    if folder["full_name"].startswith("course files"):
                        folder["full_name"] = folder["full_name"][len("course files") :]
                    res[folder["id"]] = folder["full_name"]
                    if not res[folder["id"]]:
                        res[folder["id"]] = "/"
                    res[folder["id"]] = re.sub(
                        r"[\\\:\*\?\"\<\>\|]", "_", res[folder["id"]]
                    )
                return res
            except Exception as e:
                retryTimes = retryTimes + 1
                if self.config["debug"]:
                    print(str(retryTimes) + " time(s) error: " + str(e))

    async def getCourseFilesHelper(self, page, courseID, folders):
        files = {}
        url = f"{self.baseUrl}/courses/{courseID}/files?page={page}"
        canvasFiles = await self.client.json(url, debug=self.config["debug"])
        if not canvasFiles or isinstance(canvasFiles, dict):
            return files
        for f in canvasFiles:
            if f["folder_id"] not in folders:
                continue
            f["display_name"] = re.sub(r"[\/\\\:\*\?\"\<\>\|]", "_", f["display_name"])
            path = f"{folders[f['folder_id']]}/{f['display_name']}"
            path = path.replace("\\", "/").replace("//", "/")
            if f["locked_for_user"]:
                print(
                    f"\t {self.courseCode[courseID]}{path} is locked: {f["lock_explanation"]}"
                )
                continue
            dt = datetime.strptime(f["modified_at"], "%Y-%m-%dT%H:%M:%SZ")
            modifiedTimeStamp = int(dt.replace(tzinfo=timezone.utc).timestamp())
            # response = await self.client.head(f["url"])
            # file_size = int(response.get("content-length", 0))
            file_size = int(f["size"])
            files[path] = {
                "url": f["url"],
                "modified_time": int(modifiedTimeStamp),
                "size": file_size,
            }
        return files

    def checkNewFiles(self):
        if self.skipFiles:
            print(
                "These file(s) will not be synced due to their size"
                + f" (over {self.config['filesizeThresh']} MB):"
            )
            for f in self.skipFiles:
                print(f)
        if self.newFiles:
            print(f"Ready to download {len(self.newInfo)} file(s).")
            for s in self.newInfo:
                print(s)

    def compareExistingFiles(self):
        if not self.laterFiles:
            return
        else:
            for courseID in self.laterFiles:
                if self.laterFiles[courseID]:
                    break
                else:
                    return
        # if not self.laterFiles:
        #     return
        print("\nThese file(s) have later version on canvas:")
        for courseID in self.laterFiles:
            for fileName, file_info in self.laterFiles[courseID].items():
                print(
                    f"\t{self.courseCode[courseID]}{fileName} (Modified at: {datetime.fromtimestamp(file_info['modified_time']).strftime('%Y-%m-%d %H:%M:%S')})"
                )
        isUpdate = "Y" if self.config["y"] else input("Update all?(y/n) ")
        while isUpdate.lower() not in ["y", "n"]:
            isUpdate = input("Please input 'y' or 'n': ")
        if isUpdate.lower() == "n":
            self.laterFiles.clear()
            return
        # Remove local files with older version
        for courseID in self.laterFiles:
            for fileName, file_info in self.laterFiles[courseID].items():
                abs_file_path = (
                    os.path.join(
                        self.downloadDir, f"{self.courseCode[courseID]}{fileName}"
                    )
                    .replace("\\", "/")
                    .replace("//", "/")
                )
                # TODO change local_created_time to readable time format
                local_created_time = int(os.path.getctime(abs_file_path))
                local_modified_time = int(os.path.getmtime(abs_file_path))
                try:
                    # TODO change overall option of keep_older_version to control each file;
                    # if local ctime = local mtime, no manual change, follow keep_older_version
                    # if local ctime < local mtime, manual change, force backup local
                    # BUG bad logic
                    # 获取文件名和扩展名
                    file_name, file_ext = os.path.splitext(
                        os.path.basename(abs_file_path)
                    )
                    newPath = os.path.join(
                        os.path.dirname(abs_file_path),
                        f"{file_name}_{datetime.fromtimestamp(local_modified_time).strftime('%Y%m%d_%H%M')}{file_ext}",
                    )
                    if local_created_time == local_modified_time:
                        # 说明文件没有被修改过
                        if self.config["keep_older_version"]:
                            os.rename(abs_file_path, newPath)
                        else:
                            os.remove(abs_file_path)
                    if local_created_time < local_modified_time:
                        # 说明文件被修改过，强制备份
                        os.rename(abs_file_path, newPath)

                except Exception as e:
                    print(f"\t [{e.__class__.__name__}] Skipped: {abs_file_path}")

    def checkFileType(self):
        for courseID in self.courseCode:
            items = list(self.onlineFiles[courseID].items())
            for file_name, _ in items:
                if not self.checkFileTypeHelper(courseID, file_name):
                    self.onlineFiles[courseID].pop(file_name)

    def checkFileTypeHelper(self, courseID, filename):
        fileType = (mimetypes.guess_type(filename))[0]
        if fileType is None:
            return True
        if not self.config["allowAudio"]:
            if fileType.split("/")[0] == "audio":
                print(f"Audio removed: {self.courseCode[courseID]}{filename} ")
                return False
        if not self.config["allowVideo"]:
            if fileType.split("/")[0] == "video":
                print(f"Video removed: {self.courseCode[courseID]}{filename} ")
                return False
        if not self.config["allowImage"]:
            if fileType.split("/")[0] == "image":
                print(f"Image removed: {self.courseCode[courseID]}{filename} ")
                return False
        return True

    # TODO need to integrade with compareExistingFiles
    def categorizeFiles(self):
        for courseID in self.courseCode:
            if courseID not in self.laterFiles:
                self.laterFiles[courseID] = {}
            if courseID not in self.newFiles:
                self.newFiles[courseID] = {}
            for file_name, online_file_info in self.onlineFiles[courseID].items():
                if not self.localFiles[courseID]:
                    self.newFiles[courseID][file_name] = online_file_info
                else:
                    if file_name in self.localFiles[courseID]:
                        # TODO logic changed, move comparsion to compareExistingFiles. check validity
                        if (
                            online_file_info["modified_time"]
                            > self.localFiles[courseID][file_name]["created_time"]
                        ):
                            self.laterFiles[courseID][file_name] = online_file_info
                        # self.laterFiles[courseID][file_name] = online_file_info
                        # else:
                        #     pass
                        #     # print(
                        #     # f"{self.courseCode[courseID]}{file_name} has newer local version. Removed from download list."
                        #     # )
                    else:
                        self.newFiles[courseID][file_name] = online_file_info

    def checkFileSize(self):
        for courseID in self.courseCode:
            if courseID not in self.overSizedFiles:
                self.overSizedFiles[courseID] = {}
            items = list(self.onlineFiles[courseID].items())
            for file_name, file_info in items:
                if file_info["size"] > self.config["filesizeThresh"] * 1024 * 1024:
                    self.overSizedFiles[courseID][file_name] = file_info
                    self.onlineFiles[courseID].pop(file_name)
            for file_name, file_info in self.overSizedFiles[courseID].items():
                print(
                    f"File too large: {self.courseCode[courseID]}{file_name} ({file_info['size'] / 1024 / 1024:.2f} MB)"
                )
        print("")

    def prepareDownload(self):
        for courseID in self.newFiles:
            if courseID not in self.downloadList:
                self.downloadList[courseID] = {}
            if self.newFiles:
                self.downloadList[courseID].update(self.newFiles[courseID])
            if self.laterFiles:
                self.downloadList[courseID].update(self.laterFiles[courseID])
        for courseID in self.downloadList:
            for file_info in self.downloadList[courseID].values():
                self.downloadSize += file_info["size"]

    # 把从laterFiles中下载的文件的创建时间改为在线文件的创建时间
    def changeLaterFilesCTime(self):
        for courseID in self.laterFiles:
            if self.laterFiles[courseID]:
                for file_name, file_info in self.laterFiles[courseID].items():
                    abs_file_path = (
                        os.path.join(
                            self.downloadDir,
                            f"{self.courseCode[courseID]}{file_name}",
                        )
                        .replace("\\", "/")
                        .replace("//", "/")
                    )
                    try:
                        # 修改文件的创建时间和修改时间
                        handle = win32file.CreateFile(
                            abs_file_path,
                            win32con.GENERIC_WRITE,
                            0,
                            None,
                            win32con.OPEN_EXISTING,
                            win32con.FILE_ATTRIBUTE_NORMAL,
                            None,
                        )
                        new_time = pywintypes.Time(file_info["modified_time"])
                        win32file.SetFileTime(handle, new_time, None, None)
                        handle.close()
                        os.utime(
                            abs_file_path,
                            (file_info["modified_time"], file_info["modified_time"]),
                        )
                    except Exception as e:
                        print(f"Error: {e}")

    async def sync(self):
        # Get course IDs
        times = 0
        total_course_number = len(self.config.get("courseCodes", [])) + len(
            self.config.get("courseIDs", [])
        )
        print("Getting course IDs...")
        all_found = False
        while times < FIND_COURSE_RETRY_TIMES - 1:
            await self.getCourseID()
            if len(self.courseCode) == total_course_number:
                all_found = True
                break
            else:
                print("Number of available courses doesn't match, retrying...")
            times += 1
        if not all_found:
            await self.getCourseID()
            if len(self.courseCode) == total_course_number:
                all_found = True
        if not all_found:
            print(
                "Failed to get all course. Check your course code or course ID format."
            )
            isContinue = "Y" if self.config["y"] else input("\t Continue?(y/n) ")
            if isContinue.lower() == "n":
                exit()

        print(f"Get {len(self.courseCode)} available courses.\n")

        # Get files
        print("Finding files on canvas...")
        # Get online files and folders
        await asyncio.gather(
            *[
                asyncio.create_task(self.getCourseFiles(courseID))
                for courseID in self.courseCode
            ]
        )
        # Get offline files
        for courseID in self.courseCode:
            self.localFiles[courseID] = self.scanLocalFiles(
                courseID, self.folders[courseID]
            )

        print(
            f"Found {self.countFiles(self.onlineFiles)} downloadable files.\n\nPreparing to sync..."
        )

        # Check file size
        self.checkFileSize()
        self.checkFileType()
        self.categorizeFiles()
        self.compareExistingFiles()
        self.prepareDownload()

        if not self.downloadSize:
            return print("All local files are synced!")
        else:
            print(f"Prepare to download {self.countFiles(self.downloadList)} files:")
            for courseID in self.downloadList:
                if self.downloadList[courseID]:
                    for files in self.downloadList[courseID]:
                        print(f"\t{self.courseCode[courseID]}{files}")
            await self.client.downloadMany(
                self.downloadDir, self.downloadList, self.downloadSize, self.courseCode
            )
            self.changeLaterFilesCTime()

            print("Sync completed!")

        return
