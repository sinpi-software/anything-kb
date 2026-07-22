# ChatOpenAI(
#     model=model_name,
#     base_url=OPENROUTER_BASE_URL,
#     api_key=SecretStr(OPENROUTER_API_KEY) if OPENROUTER_API_KEY else None,
#     temperature=0,
#     timeout=LLM_TIMEOUT,  # bound the call so a hung request can't wedge a runner slot
#     max_retries=0,  # our _invoke_with_retry owns retries; don't let langchain stack its own
#     max_completion_tokens=LLM_MAX_TOKENS,  # langchain 1.x name for max_tokens
#     # OpenRouter usage accounting goes in the request BODY (extra_body), not
#     # model_kwargs — langchain forwards model_kwargs as call kwargs, and a `usage`
#     # kwarg makes the OpenAI client raise TypeError, breaking every LLM call.
#     extra_body={
#         "usage": {"include": True},
#         **({"reasoning": {"effort": LLM_REASONING_EFFORT}} if LLM_REASONING_EFFORT else {}),
#     },
# )
