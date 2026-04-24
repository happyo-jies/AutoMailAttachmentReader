
from openai import OpenAI

messages = [
    {
        "role": "user",
        "content": "直接查武汉未来5天"
    },
]

def main():
    headers = {
        'Accept': "*/*",
        'Accept-Encoding': "gzip, deflate, br",
        'User-Agent': "PostmanRuntime-ApipostRuntime/1.1.0",
        'Connection': "keep-alive",
        'Content-Type': "application/json"
    }

    openai_api_base = "http://lightcode-uis.hundsun.com:8080/uis/v1"
    openai_api_key = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYWlxanM0MTg0OCIsImlhdCI6MTc3Mjc4MDY1OH0.ohmoaOVh9s52hQup5v9kk4cL9CXu88_F6aHFPiEgxCA"

    client = OpenAI(
        api_key=openai_api_key,
        base_url=openai_api_base,  # 注意：基础url, 不是完整url地址
        default_headers=headers    # 使用：公司内部uis地址，则必须配置对应的header
    )

    model = "gpt-5.4"
    chat_completion = client.chat.completions.create(
        messages=messages,
        model=model,
        # tools=tools,
        # tool_choice="required",
        stream=True  # Enable streaming response
    )
    
    # 新增逻辑：打印AI的回复内容
    reply = ""
    for chunk in chat_completion:
        if hasattr(chunk, "choices") and chunk.choices and hasattr(chunk.choices[0].delta, "content"):
            content = chunk.choices[0].delta.content
            if content:
                print(content, end="", flush=True)
                reply += content
    print()  # 换行

main()