import os
import requests
import time
import json
from dune_client.client import DuneClient

print("=== Dune API 测试 - 执行查询并获取结果 ===")

# 清除代理
for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"]:
    if key in os.environ:
        del os.environ[key]

api_key = "X3EJql0gmljg6diFq4zvDnLjuVM5wZZX"
query_id = 5634415

print(f"正在执行查询 {query_id}...")

# 步骤1: 执行查询
execute_url = f"https://api.dune.com/api/v1/query/{query_id}/execute"
headers = {"X-DUNE-API-KEY": api_key}

response = requests.post(execute_url, headers=headers)
print(f"执行请求状态: {response.status_code}")

if response.status_code == 200:
    execution_data = response.json()
    execution_id = execution_data["execution_id"]
    state = execution_data["state"]
    
    print(f"✅ 查询执行成功!")
    print(f"执行ID: {execution_id}")
    print(f"当前状态: {state}")
    
    # 步骤2: 轮询获取结果
    result_url = f"https://api.dune.com/api/v1/execution/{execution_id}/results"
    
    print("\n正在等待查询完成...")
    max_attempts = 30  # 最多等待5分钟
    
    for attempt in range(max_attempts):
        time.sleep(10)  # 每10秒检查一次
        
        result_response = requests.get(result_url, headers=headers)
        if result_response.status_code == 200:
            result_data = result_response.json()
            execution_state = result_data.get("state", "")
            
            print(f"尝试 {attempt + 1}: 状态 = {execution_state}")
            
            if execution_state == "QUERY_STATE_COMPLETED":
                print("🎉 查询执行完成!")
                
                # 显示结果信息
                result = result_data.get("result", {})
                rows = result.get("rows", [])
                metadata = result.get("metadata", {})
                
                print(f"返回行数: {len(rows)}")
                print(f"列信息: {[col['name'] for col in metadata.get('column_names', [])]}")
                
                if rows:
                    print(f"\n前3行数据:")
                    for i, row in enumerate(rows[:3]):
                        print(f"  行 {i+1}: {row}")
                
                break
            elif execution_state == "QUERY_STATE_FAILED":
                print("❌ 查询执行失败")
                error_msg = result_data.get("error", "未知错误")
                print(f"错误信息: {error_msg}")
                break
            elif execution_state in ["QUERY_STATE_PENDING", "QUERY_STATE_EXECUTING"]:
                continue  # 继续等待
            else:
                print(f"⚠ 未知状态: {execution_state}")
                break
        else:
            print(f"❌ 获取结果失败: {result_response.status_code}")
            print(result_response.text)
            break
    else:
        print("⏰ 查询执行超时，请稍后手动检查结果")
        print(f"可以访问: https://dune.com/queries/{query_id}")

else:
    print(f"❌ 执行查询失败: {response.status_code}")
    print(response.text)

print("\n=== 测试完成 ===")
print("如果执行成功，说明你的Dune API配置完全正确！")
print("可以在你的应用中使用类似的方式来执行查询。")