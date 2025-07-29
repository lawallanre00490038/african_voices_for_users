-- Make sure to use a valid UUID or generate one dynamically
INSERT INTO dataset (id, name, description, created_by, created_at)
VALUES (
    'hausa',  -- Replace with your desired or with the language name instead
    'Sample Dataset',
    'This is a test dataset entry',
    '340e1bd4-ef19-4b67-9074-b011a5b8a919',  -- Replace with your user ID
    NOW()
);
