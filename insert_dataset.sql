-- Make sure to use a valid UUID or generate one dynamically
INSERT INTO dataset (id, name, description, created_by, created_at)
VALUES (
    'hausa',  -- Replace with your desired or with the language name instead
    'Sample Dataset',
    'This is a test dataset entry',
    'edbabb97-24ba-4ab2-8f52-b775f1e2fb09',  -- Replace with your user ID
    NOW()
);
