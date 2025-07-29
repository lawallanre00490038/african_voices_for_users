-- =====================================================================
-- Seed AudioSample rows for Hausa dataset
-- Usage (psql):
--   psql -d african_voices -f seed_hausa_audios.sql -v dataset_id='YOUR_DATASET_UUID'
-- Make sure the provided :dataset_id exists in table "dataset".
-- Requires extension pgcrypto for gen_random_uuid().
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

BEGIN;

-- Insert 4 Hausa samples. Note: constraints expect lowercase values
-- for language ('hausa') and gender ('male'/'female').

INSERT INTO audiosample (
    id,
    dataset_id,
    audio_path,
    duration,
    transcript,
    speaker_id,
    transcript_id,
    language,
    sample_rate,
    snr,
    approval,
    gender,
    age,
    education,
    domain,
    uploaded_at,
    created_at
) VALUES
(
    gen_random_uuid(),
    :'dataset_id',
    'data/hau_m_HS1M2_AK1_001.wav',
    4.714,
    'Nakan yi wa marayun murmushi duk lokacin da na gan su.',
    'HS1M2',
    'hau_AK1_001',
    'hausa',
    80000,
    40.0,
    'approved',
    'male',
    25,
    NULL,
    'Finance',
    NOW(),
    NOW()
),
(
    gen_random_uuid(),
    :'dataset_id',
    'data/hau_m_HS1M2_AK1_002.wav',
    4.474,
    'Binta tana ba su ruwa tun kafin ma su nema.',
    'HS1M2',
    'hau_AK1_002',
    'hausa',
    80000,
    40.0,
    'approved',
    'male',
    25,
    NULL,
    'Finance',
    NOW(),
    NOW()
),
(
    gen_random_uuid(),
    :'dataset_id',
    'data/hau_m_HS1M2_AK1_003.wav',
    5.154,
    'Mukan yi wa mutane sannu duk lokacin da muka ga suna aiki.',
    'HS1M2',
    'hau_AK1_003',
    'hausa',
    80000,
    40.0,
    'approved',
    'male',
    25,
    NULL,
    'Finance',
    NOW(),
    NOW()
),
(
    gen_random_uuid(),
    :'dataset_id',
    -- If your source file for the 4th row is actually 004.wav, change the next line accordingly.
    'data/hau_m_HS1M2_AK1_004.wav',
    5.674,
    'Tana da kirki da mutunci sosai, domin tana gaishe ni koyaushe.',
    'HS1M2',
    'hau_AK1_004',
    'hausa',
    80000,
    40.0,
    'approved',
    'male',
    25,
    NULL,
    'Finance',
    NOW(),
    NOW()
);

COMMIT;

-- Verify
-- SELECT id, audio_path, transcript FROM audiosample ORDER BY created_at DESC LIMIT 10;
