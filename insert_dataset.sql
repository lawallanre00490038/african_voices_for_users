-- Make sure to use a valid UUID or generate one dynamically
INSERT INTO dataset (id, name, description, created_by, created_at)
VALUES (
    'c9fe5b7e-5e90-4a3b-b623-0123456789ab',  -- Replace with your desired or with the language name instead
    'Sample Dataset',
    'This is a test dataset entry',
    '13233b30-37df-4a24-a23e-3752935114a6',  -- Replace with your user ID
    NOW()
);
