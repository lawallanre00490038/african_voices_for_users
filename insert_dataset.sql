-- Make sure to use a valid UUID or generate one dynamically
INSERT INTO dataset (id, name, description, created_by, created_at)
VALUES (
    'hausa',  -- Replace with your desired or with the language name instead
    'Sample Dataset',
    'This is a test dataset entry',
    '74657c64-8b48-49fb-9ef4-aaced1084fea',  -- Replace with your user ID
    NOW()
);
