-- Add platform-level VLM and reranker model overrides.
-- value = 'null'  → no platform override (tenant pref or env fallback wins)
-- value = '"qwen2.5-vl:3b:4bit"' → platform-wide override
INSERT INTO admin.platform_settings (key, value)
VALUES ('llm_vlm_model',    'null'),
       ('llm_rerank_model', 'null')
ON CONFLICT (key) DO NOTHING;
