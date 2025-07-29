-- Make sure to use a valid UUID or generate one dynamically
INSERT INTO dataset (id, name, description, created_by, created_at)
VALUES (
    'hausa',  -- Replace with your desired or with the language name instead
    'Sample Dataset',
    'This is a test dataset entry',
    'af8302b1-e4b7-4fc2-8a74-a88576f0717f',  -- Replace with your user ID
    NOW()
);
