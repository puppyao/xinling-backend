import requests
import json

BASE_URL = "http://127.0.0.1:8080"
USER_ID = "12345"

def test_api(name, method, url, expected_code=None, expected_http=None, data=None, custom_headers=None):
    # 设置请求头
    headers = {"X-User-Id": USER_ID, "Content-Type": "application/json"}
    if custom_headers is not None:
        headers = custom_headers
    
    print(f"\n▶ 测试: {name}")
    print(f"URL: {url}")
    
    try:
        if method == "GET":
            response = requests.get(url, headers=headers)
        elif method == "POST":
            response = requests.post(url, headers=headers, json=data)
        elif method == "DELETE":
            response = requests.delete(url, headers=headers)
        elif method == "PATCH":
            response = requests.patch(url, headers=headers, json=data)
        
        print(f"HTTP状态码: {response.status_code}")
        if expected_http:
            print(f"预期HTTP: {expected_http} {'✓' if response.status_code == expected_http else '✗'}")
        
        # 解析JSON
        result = response.json()
        print(f"返回数据: {json.dumps(result, ensure_ascii=False, indent=2)}")
        
        if expected_code is not None:
            print(f"业务code: {result.get('code')} {'✓' if result.get('code') == expected_code else '✗'}")
        
        return result
    except Exception as e:
        print(f"❌ 错误: {e}")

# ===== 音乐播放模块 =====
print("\n========== 音乐播放模块 ==========")

# 1. 推荐音乐 - 成功
test_api("推荐音乐(焦虑)", "GET", f"{BASE_URL}/api/music/recommend?emotion=anxious", 0, 200)

# 2. 推荐音乐 - 参数错误 (1001)
test_api("推荐音乐(无参数)", "GET", f"{BASE_URL}/api/music/recommend", 1001, 400)

# 3. 获取状态 - 成功
test_api("获取状态", "GET", f"{BASE_URL}/api/music/status", 0, 200)

# 4. 获取状态 - 未授权 (1003)
test_api("获取状态(无用户)", "GET", f"{BASE_URL}/api/music/status", 1003, 401, custom_headers={})

# 5. 播放指定曲目 - 成功
test_api("播放指定曲目", "POST", f"{BASE_URL}/api/music/play", 0, 200, {"musicId": "m001"})

# 6. 播放指定曲目 - 资源不存在 (1002)
test_api("播放不存在的曲目", "POST", f"{BASE_URL}/api/music/play", 1002, 404, {"musicId": "m999"})

# 7. 进度拖拽 - 参数错误 (1001)
test_api("进度拖拽(参数错误)", "POST", f"{BASE_URL}/api/music/seek", 1001, 400, {"position": 9999})

# 8. 音量调节 - 参数错误 (1001)
test_api("音量调节(参数错误)", "POST", f"{BASE_URL}/api/music/volume", 1001, 400, {"volume": 2.5})

# 9. 播放模式 - 参数错误 (1001)
test_api("播放模式(参数错误)", "POST", f"{BASE_URL}/api/music/play-mode", 1001, 400, {"mode": "invalid"})

# 10. 获取分类 - 成功
test_api("获取分类", "GET", f"{BASE_URL}/api/music/categories", 0, 200)

# ===== 数据存储模块 =====
print("\n========== 数据存储模块 ==========")

# 11. 保存心情记录 - 成功
test_api("保存心情", "POST", f"{BASE_URL}/api/moods", 0, 200, {
    "date": "2026-03-17", "emotion": "happy", "note": "测试"
})

# 12. 保存心情 - 参数错误 (1001)
test_api("保存心情(无日期)", "POST", f"{BASE_URL}/api/moods", 1001, 400, {
    "emotion": "happy"
})

# 13. 收藏音乐 - 成功
result = test_api("收藏音乐", "POST", f"{BASE_URL}/api/favorites", 0, 200, {
    "musicId": "m001", "title": "测试", "artist": "工作室", "coverUrl": "/test.jpg"
})

# 14. 重复收藏 - 参数错误 (1001) 注意是409+1001
test_api("重复收藏", "POST", f"{BASE_URL}/api/favorites", 1001, 409, {
    "musicId": "m001", "title": "测试", "artist": "工作室", "coverUrl": "/test.jpg"
})

# 15. 取消收藏 - 成功
test_api("取消收藏", "DELETE", f"{BASE_URL}/api/favorites/m001", 0, 200)

# 16. 取消收藏 - 资源不存在 (1002)
test_api("取消不存在的收藏", "DELETE", f"{BASE_URL}/api/favorites/m999", 1002, 404)

# 17. 添加最近播放 - 成功
test_api("添加最近播放", "POST", f"{BASE_URL}/api/recently-played", 0, 200, {
    "musicId": "m001", "title": "测试", "artist": "工作室", "coverUrl": "/test.jpg"
})

# 18. 获取最近播放 - 成功
test_api("获取最近播放", "GET", f"{BASE_URL}/api/recently-played?limit=5", 0, 200)

# 19. 情绪统计饼图 - 成功
test_api("情绪统计饼图", "GET", f"{BASE_URL}/api/stats/emotions/pie", 0, 200)

# 20. 保存问卷 - 成功
test_api("保存问卷", "POST", f"{BASE_URL}/api/questionnaire", 0, 200, {
    "date": "2026-03-17", "type": "SAS", "score": 45, "level": "中度焦虑"
})

# 21. 获取问卷 - 资源不存在 (1002) 或成功
test_api("获取最新问卷", "GET", f"{BASE_URL}/api/questionnaire/latest", None, 200)

# 22. 保存用户设置 - 成功
test_api("保存用户设置", "PATCH", f"{BASE_URL}/api/user/preferences", 0, 200, {
    "preferredMusicGenres": ["classical", "lofi"]
})

# 23. 获取用户设置 - 成功
test_api("获取用户设置", "GET", f"{BASE_URL}/api/user/preferences", 0, 200)

print("\n========== 测试完成 ==========")