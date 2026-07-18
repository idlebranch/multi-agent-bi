"""Manual provider connectivity check; not part of the offline test suite."""

from src.config import get_llm


if __name__ == "__main__":
    response = get_llm(0.0).invoke("请用一句话确认模型连接正常。")
    print(response.content)
