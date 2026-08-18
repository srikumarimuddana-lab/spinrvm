-- ============================================================
-- Driver Bank/SIN Migration Script
-- Source: Old system MongoDB bank export
-- Total bank records: 157
-- Generated: 2026-08-14 02:04:45 UTC
--
-- SECURITY: SINs are inserted as plaintext into a staging table,
-- then encrypted via encrypt_driver_pii() Vault RPC before
-- being written to drivers. The staging table is dropped at the end.
-- ============================================================

-- STEP 1: Create staging table for bank/SIN data
DROP TABLE IF EXISTS driver_bank_import;

CREATE TABLE driver_bank_import (
    id SERIAL PRIMARY KEY,
    old_id TEXT,
    old_driver_id TEXT,
    first_name TEXT,
    last_name TEXT,
    phone TEXT,
    email TEXT,
    date_of_birth DATE,
    sin_raw TEXT,              -- plaintext, encrypted in STEP 4, purged in STEP 7
    sin_last4 TEXT,
    gst_bn TEXT,
    stripe_account_id TEXT,
    account_number TEXT,
    institute_number TEXT,
    transit_number TEXT,
    address JSONB,
    country TEXT,
    old_created_at TIMESTAMPTZ,
    matched_user_id UUID,
    sin_vault_id TEXT,          -- filled by STEP 4 (encrypted)
    migration_status TEXT DEFAULT 'pending'
);

-- STEP 2: Insert bank records from old CSV

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('697ceb3b8cd7f3775ff5232b', '697ce4e68cd7f3775ff51f6e', 'Yash', 'Kumar', '3062929175', '1992-08-03', '691694459', '4459', '123456789RT0001', 'acct_1SvLYhJz1ZuONHHr', '5102421', '003', '07418', '{"line1": "4321 Wakeling Street", "city": "Regina", "state": "Saskatchewan", "postal_code": "S4W 0L7"}'::jsonb, 'ca', '2026-01-30 17:32:34+00'),
    ('6985e6304de23d6004a25a74', '6985d4374de23d6004a1e59f', 'Karan', 'bir', '7505008079', '2001-02-06', '123456789', '6789', '22AAAAA0000A1Z5', 'acct_1SxofAFAylKtG2Nd', '000123456789', '000', '00011', '{"line1": "7460 Edmonds Street", "city": "Burnaby", "state": "British Columbia", "postal_code": "V3N 1B2"}'::jsonb, 'ca', '2026-02-06 13:01:30+00'),
    ('6985e9204de23d6004a26587', '6985e7204de23d6004a26300', 'deep', 'anshu', '6444444191', '1995-02-06', '123456789', '6789', '07AAAAA0000A1Z1', 'acct_1SxorHFGtQr56STm', '000123456789', '000', '00011', '{"line1": "7460 Edmonds Street", "city": "Burnaby", "state": "British Columbia", "postal_code": "V3N 1B2"}'::jsonb, 'ca', '2026-02-06 13:14:02+00'),
    ('699478d2a584c10c16b1a972', '69947469a584c10c16b1a5bc', 'satbir', 'singh', '4313740087', '1998-01-15', '940068927', '8927', '726383706rt0001', 'acct_1T1p74F0CDq2Xr0v', '5643090', '010', '01708', '{"line1": "1610 College Avenue", "city": "Regina", "state": "Saskatchewan", "postal_code": "S4P 1B7"}'::jsonb, 'ca', '2026-02-17 14:18:50+00'),
    ('69a107ac307b6864a0addc5b', '699bafaba584c10c16b55436', 'Gurpreet', 'singh', '5145723946', '1997-09-24', '954465753', '5753', '764945663 RT0001', 'acct_1T5HDIFEzzpbPM0x', '428610681083', '002', '42861', '{"line1": "Labine Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L5Y4"}'::jsonb, 'ca', '2026-02-27 02:55:29+00'),
    ('69a12d93307b6864a0ae3c1c', '699f3d0ca584c10c16b77a82', 'Md Nazmul', 'huda', '6394706707', '1989-06-29', '966954000', '4000', '735587230rt0001', 'acct_1T5JjmF5dYfhIVDe', '5999391', '010', '00018', '{"line1": "33rd Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R0M5"}'::jsonb, 'ca', '2026-02-27 05:37:15+00'),
    ('69a13f83307b6864a0ae4b2a', '69a12a20307b6864a0ae35a9', 'Nazim', 'Howlader', '3067168785', '1976-06-13', '671705697', '5697', '794655878RT0001', 'acct_1T5KvrF10GfFF7vd', '5381900', '003', '07758', '{"line1": "1814 22 Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 0T7"}'::jsonb, 'ca', '2026-02-27 06:53:45+00'),
    ('69a1f061a7a37f75bce967aa', '699a1d14a584c10c16b4633e', 'GURBIR', 'SINGH', '3062120304', '1978-04-15', '683501597', '1597', '763306289RT0001', 'acct_1TH73VFTAt2bXE7B', '3978163', '001', '06188', '{"line1": "937 Northumberland Avenue", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 3W8"}'::jsonb, 'ca', '2026-02-27 19:28:25+00'),
    ('69a358d6a7a37f75bceac6d3', '6993ab4ca584c10c16b14cab', 'Md Amjad Ahmed', 'Chowdhury', '6395250131', '2000-04-12', '686991225', '1225', '705829562317281', 'acct_1T5uiU2UNbnMkeeL', '3904463', '001', '06188', '{"line1": "434 McArthur Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6Y3"}'::jsonb, 'ca', '2026-02-28 21:06:20+00'),
    ('69a48ed7a7a37f75bcec0ef5', '69a47b6da7a37f75bcebff40', 'Tahir', 'Arshad', '3068809888', '1975-07-06', '686520149', '0149', '792653149RT0001', 'acct_1T6FMVFSsX7qrDkV', '6242189', '010', '01018', '{"line1": "4151 33rd Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R 0M4"}'::jsonb, 'ca', '2026-03-01 19:09:02+00'),
    ('69a5d0d5a7a37f75bced7570', '699c8ff6a584c10c16b5bd01', 'aakash', 'arora', '4384650786', '1994-07-04', '698673936', '3936', '744652553RT0001', 'acct_1T6ao1FKKb42IsAJ', '6943078', '004', '44001', '{"line1": "1235 Kensington Manor", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6S5"}'::jsonb, 'ca', '2026-03-02 18:02:51+00'),
    ('69a8acb9a7a37f75bcf0a47e', '6997214da584c10c16b2df85', 'Emmanuel', 'Aku', '3068801744', '1984-12-20', '153392915', '2915', '000000153392915', 'acct_1T7NY0FYwibKWW4o', '5000807', '003', '07408', '{"line1": "160 Marlatte Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0W5"}'::jsonb, 'ca', '2026-03-04 22:05:36+00'),
    ('69af34aca7a37f75bcf9680d', '6993c694a584c10c16b157fd', 'Tuseef', 'Ahmad', '3067158743', '1984-01-09', '673298543', '8543', '772580130RT0001', 'acct_1T9AtZFGruChYLvC', '6034364', '004', '77348', '{"line1": "125 Stonebridge Common", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0Z5"}'::jsonb, 'ca', '2026-03-09 20:59:15+00'),
    ('69b0d61ba7a37f75bcfbe5d5', '699d6beaa584c10c16b64776', 'Manpreet', 'singh', '6394714530', '1992-09-08', '688122951', '2951', '732282546RT0001', 'acct_1TEMdGFA7gHD1opz', '3943335', '001', '34658', '{"line1": "147 Maningas Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7w0L4"}'::jsonb, 'ca', '2026-03-11 02:40:19+00'),
    ('69b3a2d3fb14d015b75c4108', '69b377c5fb14d015b75c0dd8', 'Md  Kamal', 'Hossain', '3067155774', '1978-10-10', '673997557', '7557', '790188403RT0001', 'acct_1TPx1uFaK7s6F7Md', '5450259', '003', '07758', '{"line1": "122 Saint Paul''s Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 0H1"}'::jsonb, 'ca', '2026-03-13 05:38:19+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('69b6dd02fb14d015b76380fc', '69a89f2ba7a37f75bcf07cc4', 'Vishal', 'Patel', '3063032880', '1984-04-06', '591433073', '3073', '728081134RT0001', 'acct_1TBHRqFT1okbXpBw', '6985672', '004', '01362', '{"line1": "550 Redberry Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 4S3"}'::jsonb, 'ca', '2026-03-15 16:23:22+00'),
    ('69b6f0a7fb14d015b763ac21', '69adc2f2a7a37f75bcf7589f', 'Shamas', 'ud din', '3067158248', '1976-01-18', '674620885', '0885', '777730276RT0001', 'acct_1TBIkx2aySJ62RbM', '6095626', '004', '76668', '{"line1": "385 Kingsmere Boulevard", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 4J6"}'::jsonb, 'ca', '2026-03-15 17:47:09+00'),
    ('69b731e9fb14d015b7646f6d', '699ce351a584c10c16b5f8f9', 'Prit', 'Patel', '6395973348', '1998-12-10', '695333047', '3047', '78212 2436 RT000', 'acct_1THByFF3sCq7maNC', '1040542', '003', '07758', '{"line1": "2807 7th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 1A9"}'::jsonb, 'ca', '2026-03-15 22:25:37+00'),
    ('69b77accfb14d015b764ed50', '69b745dffb14d015b7649d42', 'Amitkumar', 'Darji', '6399161976', '1976-06-11', '687807875', '7875', '776678666RT0001', 'acct_1TBRxMF6R0dpT9Zw', '5233872', '004', '77408', '{"line1": "330 Prasad Union", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 1L7"}'::jsonb, 'ca', '2026-03-16 03:36:36+00'),
    ('69b77b11fb14d015b764ee09', '69a79a1ea7a37f75bcef889c', 'Azhar', 'Mahmood', '3069141942', '1962-06-28', '683365787', '5787', '701595480RT0001', 'acct_1TI9LEJtiycWuyNF', '1100211175', '889', '61838', '{"line1": "1703 Patrick Cres", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0L6"}'::jsonb, 'ca', '2026-03-16 03:37:43+00'),
    ('69b82614fb14d015b765eb74', '699a81f9a584c10c16b4979a', 'Jagneet', 'singh', '6393186610', '2002-03-20', '694133885', '3885', '720482942RT0001', 'acct_1TBdMZFI3O0Qapx7', '0104922', '002', '55848', '{"line1": "212 10th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 2T6"}'::jsonb, 'ca', '2026-03-16 15:47:22+00'),
    ('69b8c41bfb14d015b7680607', '6995d115a584c10c16b2411e', 'Gurpreet', 'singh', '6393847430', '1991-12-21', '687852897', '2897', '787537216RT0001', 'acct_1TBnt4F8MEcyu2Nl', '5064779', '003', '01429', '{"line1": "538 West Hampton Boulevard", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R 0C3"}'::jsonb, 'ca', '2026-03-17 03:01:38+00'),
    ('69b941c05fc1dc6d1e2aa208', '69a79dc0a7a37f75bcef8bb7', 'Furqan Ul Haq', 'Muhammad', '3864912370', '1988-11-24', '686471319', '1319', '769622549RT0001', 'acct_1TBwFrFYMzO5NCKS', '6490147', '004', '77408', '{"line1": "1303 Richardson Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R 0L1"}'::jsonb, 'ca', '2026-03-17 11:57:43+00'),
    ('69bac6d75fc1dc6d1e2e6dcc', '69a58332a7a37f75bcece3ea', 'Fateh', 'Muhammad', '6395975757', '1987-10-22', '976032037', '2037', '779950369RT0001', 'acct_1TCMARFJ54mL07Za', '6886152', '004', '77368', '{"line1": "3914 Taylor Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 5J4"}'::jsonb, 'ca', '2026-03-18 15:37:50+00'),
    ('69bad0225fc1dc6d1e2e8648', '69966e21a584c10c16b29003', 'Toshak', 'Toshak', '6479393937', '1999-07-30', '953983970', '3970', '798808572 RT0001', 'acct_1TCMmpF4ZV9558q2', '6614563', '004', '77408', '{"line1": "1128 McKercher Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 4Y7"}'::jsonb, 'ca', '2026-03-18 16:17:26+00'),
    ('69baf7f45fc1dc6d1e2ecf14', '69b0478ca7a37f75bcfb26d3', 'Kartik', 'Arora', '6399942999', '1986-02-12', '692099955', '9955', '760986034RT0001', 'acct_1TCPRFFZM6J1J1jk', '1988326', '001', '06028', '{"line1": "703 Marlatte Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 1J8"}'::jsonb, 'ca', '2026-03-18 19:07:26+00'),
    ('69befc7b5fc1dc6d1e37700b', '69a1c572a7a37f75bce90244', 'Ebenizer', 'Muyoh', '5142123534', '1980-09-06', '302774971', '4971', '788686806RT0001', 'acct_1TDVw4FJXny3oU3n', '5244819', '003', '07488', '{"line1": "150 Boykowich Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0S4"}'::jsonb, 'ca', '2026-03-21 20:15:46+00'),
    ('69bf8f9a5fc1dc6d1e38c9f7', '699b934da584c10c16b54531', 'Samuel', 'Agyemang', '3069148710', '1989-08-17', '691242895', '2895', '000000717674964', 'acct_1TDfjXJzBEaR5rX6', '5138458', '003', '07488', '{"line1": "108 Atton Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7S 1M2"}'::jsonb, 'ca', '2026-03-22 06:43:32+00'),
    ('69c187975fc1dc6d1e3de487', '69a6fed0a7a37f75bceedd08', 'Parvez', 'Ahmad', '3063077346', '1968-01-12', '690343546', '3546', '79447 4379 RT0001', 'acct_1TEDIWFYX1M9UDcI', '5353289', '010', '00068', '{"line1": "11th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N0E5"}'::jsonb, 'ca', '2026-03-23 18:33:54+00'),
    ('69c188835fc1dc6d1e3de57a', '69bfd9055fc1dc6d1e398d54', 'Emmanuel', 'Omosebi', '3062294953', '1974-05-26', '695201970', '1970', '734991235RT0001', 'acct_1TEDMJFMAzoDaO49', '0157481', '002', '00588', '{"line1": "831 Kingsmere Boulevard", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 4C4"}'::jsonb, 'ca', '2026-03-23 18:37:47+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('69c31cd1a151e72db5f0b77f', '69c2aa76a151e72db5eff0fa', 'Ammad', 'Anwar', '3062032836', '1984-10-18', '673097671', '7671', '808130108rt0001', 'acct_1TEeHiFOzkCBRgsG', '123823378', '623', '80002', '{"line1": "101 106th Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 1N4"}'::jsonb, 'ca', '2026-03-24 23:22:50+00'),
    ('69c513bfed80440946065869', '69b1a4a5fb14d015b7585366', 'Muhammad', 'Sarwar', '6399162557', '1986-11-09', '685303281', '3281', '752273003RT0001', 'acct_1TFBmKFKbS7aWjTo', '6460310', '004', '77408', '{"line1": "327 Laycock Lane", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0K2"}'::jsonb, 'ca', '2026-03-26 11:08:41+00'),
    ('69c57699ed80440946072905', '69ab556aa7a37f75bcf4d777', 'Hardeep', 'singh', '4372563506', '2002-10-17', '953109931', '9931', '79454 0971 RT0001', 'acct_1TFIMUFJLxTFIxfP', '6220153', '004', '77238', '{"line1": "529 Avenue S South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 3A3"}'::jsonb, 'ca', '2026-03-26 18:10:25+00'),
    ('69c5a128ed8044094607bef2', '69bb4b4d5fc1dc6d1e2f53c5', 'Kushal', 'patel', '6395255288', '1997-06-07', '694899949', '9949', '000000702446162', 'acct_1TFLCCJwFhJ3qK6Q', '5789230', '010', '01618', '{"line1": "130 Akhtar Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 1G2"}'::jsonb, 'ca', '2026-03-26 21:11:58+00'),
    ('69c5a3b6ed8044094607d0c1', '69a850caa7a37f75bcf02c8c', 'TUFEL', 'Pathan', '3067170167', '1979-11-07', '595215781', '5781', '787310200 RT0001', 'acct_1TFLMlFDNOHN1Qis', '1068822', '002', '52092', '{"line1": "2530 Cumberland Avenue South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 2A2"}'::jsonb, 'ca', '2026-03-26 21:22:56+00'),
    ('69c5da7aed8044094608c14a', '6997c565a584c10c16b33323', 'Harshdeep', 'Rekhi', '3062612912', '1989-09-09', '696252063', '2063', '725147565RT0001', 'acct_1TFP0tF3q7OIX8xJ', '6866305', '004', '77368', '{"line1": "2675 Meadows Parkway", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0Y6"}'::jsonb, 'ca', '2026-03-27 01:16:33+00'),
    ('69c6ed6fed804409460baef0', '69b49eddfb14d015b75dd7e9', 'syed', 'jamil', '3063813015', '1979-01-01', '550843536', '3536', '811933135rt0001', 'acct_1TFhKCFJK8omfvnv', '5178454', '003', '00557', '{"line1": "2 Lindsay Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 3E1"}'::jsonb, 'ca', '2026-03-27 20:49:43+00'),
    ('69c6ed77ed804409460baf2b', '69b49eddfb14d015b75dd7e9', 'syed', 'jamil', '3063813015', '1979-01-01', '550843536', '3536', '811933135rt0001', 'acct_1TFhKKFRuAb1U5Xn', '5178454', '003', '00557', '{"line1": "2 Lindsay Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 3E1"}'::jsonb, 'ca', '2026-03-27 20:49:51+00'),
    ('69c7e6d6ed804409460ede6a', '69c54a7fed8044094606bba2', 'Yash', 'Kumar', '3062929175', '1992-08-03', '691694459', '4459', '123456789RT0001', 'acct_1TGdz8F9sT3FpJ4K', '5102421', '003', '07418', '{"line1": "4321 Wakeling Street", "city": "Regina", "state": "Saskatchewan", "postal_code": "S4W 0L7"}'::jsonb, 'ca', '2026-03-28 14:33:49+00'),
    ('69c80080ed804409460f2ebb', '699b432aa584c10c16b4ff53', 'Johnson', 'Augustine', '5197813021', '1988-03-30', '698827953', '7953', '781677364RT0001', 'acct_1TFzdxFE5OJvwwuB', '452520587885', '002', '45252', '{"line1": "234 Pichler Lane", "city": "Saskatoo", "state": "Saskatchewan", "postal_code": "S7V 0G3"}'::jsonb, 'ca', '2026-03-28 16:23:21+00'),
    ('69c80439ed804409460f365a', '69a589d6a7a37f75bcecf090', 'Hamood', 'Gill', '3068505304', '1976-12-07', '582244836', '4836', '715177283RT0001', 'acct_1TFztK2aLuzT6dMp', '0282529', '002', '55848', '{"line1": "471 Labine Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 5y8"}'::jsonb, 'ca', '2026-03-28 16:39:14+00'),
    ('69c805eeed804409460f3a2e', '69bb2aeb5fc1dc6d1e2f2371', 'Muhammad', 'Zubair', '3063413030', '1966-07-14', '738678879', '8879', '852772334RT0001', 'acct_1TG00N2W8DPzmqRg', '0101923', '002', '50328', '{"line1": "3654 Diefenbaker Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 4X6"}'::jsonb, 'ca', '2026-03-28 16:46:31+00'),
    ('69c80996ed804409460f4554', '69b1cd45fb14d015b75882c2', 'Cumhur', 'Andsoy', '3062223848', '1973-06-18', '940316136', '6136', '723478228RT0001', 'acct_1TG0FSJtNVlagOoq', '5013339', '003', '04737', '{"line1": "325 5th Avenue North", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 2P4"}'::jsonb, 'ca', '2026-03-28 17:02:05+00'),
    ('69c80f61ed804409460f5282', '69c05fc15fc1dc6d1e3ad05e', 'sureshkumar', 'sujur ganasekararaja', '6394806154', '1987-07-01', '691952311', '2311', '722598158RT0001', 'acct_1TG0dOJuu4L4sRgM', '0378380', '002', '20628', '{"line1": "114 Palliser Court", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 4P9"}'::jsonb, 'ca', '2026-03-28 17:26:50+00'),
    ('69c810d6ed804409460f58c6', '699cccd2a584c10c16b5e600', 'Punit', 'Gupta', '3068803700', '1983-01-01', '694303694', '3694', '768841553RT0001', 'acct_1TG0jPFW0pwHDOUC', '1245880', '002', '50328', '{"line1": "303 Lowe Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7S 1P2"}'::jsonb, 'ca', '2026-03-28 17:33:02+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('69c812c8ed804409460f5ddb', '69afd6d7a7a37f75bcfa549c', 'Md Aminur', 'Rahman', '6393180902', '1984-01-01', '686422734', '2734', 'RT0001756227435', 'acct_1TG0rRJtHODNv0dN', '7847696', '010', '00418', '{"line1": "3744 Fairlight Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 4T5"}'::jsonb, 'ca', '2026-03-28 17:41:20+00'),
    ('69c81ba0ed804409460f7910', '699e8fe1a584c10c16b70d9d', 'Amanjot', 'Singh', '8259772656', '1984-10-28', '692640899', '0899', '751510827RT0001', 'acct_1TG1RxFbHJr8QO7m', '3882807', '001', '26808', '{"line1": "158 Beaudry Crescent", "city": "Martensville", "state": "Saskatchewan", "postal_code": "S0K 2T1"}'::jsonb, 'ca', '2026-03-28 18:19:06+00'),
    ('69c89476ed80440946107be7', '699c5722a584c10c16b59f98', 'Ahsan', 'Javed', '3063414347', '1992-02-25', '693714974', '4974', '727359564RT0001', 'acct_1TG9UtFbUzytO1JJ', '5594189', '003', '07758', '{"line1": "314 Marlatte Street", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0B8"}'::jsonb, 'ca', '2026-03-29 02:54:39+00'),
    ('69c8af9aed8044094610a57d', '698952804de23d6004a2d94e', 'Suneet', 'Gulati', '6393847005', '1987-11-05', '677073736', '3736', '802744177RT0001', 'acct_1TGBIxFTj5iTIHEe', '112655166', '623', '80002', '{"line1": "116 Hiebert Crescent", "city": "Martensville", "state": "Saskatchewan", "postal_code": "S0K 2T2"}'::jsonb, 'ca', '2026-03-29 04:50:28+00'),
    ('69cafade5ef0d23df60f5b42', '69938bbfa584c10c16b13f7d', 'Kamaldeep', 'thapar', '9059099700', '1979-07-15', '963678073', '8073', 'nogstnumber0001', 'acct_1TGoPnFOev9ysf5I', '5015052', '003', '07488', '{"line1": "732 8 Street East#118", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0R3"}'::jsonb, 'ca', '2026-03-30 22:36:05+00'),
    ('69cb045b5ef0d23df60f96bb', '69a53e5fa7a37f75bcecaca9', 'labeed', 'ahmad', '6395255120', '2004-03-29', '695728162', '8162', '79270 4975 RT0001', 'acct_1TGp2yJwFxvRS0l1', '6242750', '004', '76668', '{"line1": "202 Whalley Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0E6"}'::jsonb, 'ca', '2026-03-30 23:16:35+00'),
    ('69cb22255ef0d23df61079a3', '69ba131a5fc1dc6d1e2ce730', 'adil', 'patel', '6477013843', '2000-06-01', '698507951', '7951', '721559425RT0001', 'acct_1TGr1yFawxNGEnU8', '0039780', '002', '65532', '{"line1": "3331 14 Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0B5"}'::jsonb, 'ca', '2026-03-31 01:23:40+00'),
    ('69cb26c85ef0d23df610a4b4', '69a86174a7a37f75bcf03c30', 'Salman Yunus', 'Patel', '4167260364', '1994-10-19', '146854575', '4575', '752424762RT0001', 'acct_1TGrL7FbJUgj94Zq', '713321374788', '002', '71332', '{"line1": "304-115 Avenue V South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 3C9"}'::jsonb, 'ca', '2026-03-31 01:43:27+00'),
    ('69cb3f365ef0d23df610fb31', '69aa5db5a7a37f75bcf3b464', 'Khalid', 'Virk', '4379909494', '1971-01-02', '518253372', '3372', '78718 0876 RT0001', 'acct_1TGsxyJwf9xIJYy3', '8366136', '010', '03702', '{"line1": "1328 Avenue H North", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 2E4"}'::jsonb, 'ca', '2026-03-31 03:27:44+00'),
    ('69cb515f5ef0d23df6111b48', '69c1d99b5fc1dc6d1e3eb383', 'Zulfiqar', 'Kamboh', '3067138766', '1972-01-01', '574819108', '9108', '722987542RT0001', 'acct_1TGuAyFOswqYP7ug', '6593813', '004', '77368', '{"line1": "7th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0Z3"}'::jsonb, 'ca', '2026-03-31 04:45:12+00'),
    ('69cc83d05ef0d23df6191515', '69a0bdaf307b6864a0ad404e', 'Roshan', 'Ali', '3067134590', '1974-08-05', '149263998', '3998', '768441016RT0001', 'acct_1THEaGF7r1czeldR', '5173224', '003', '07748', '{"line1": "122 Shillington Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 5Y7"}'::jsonb, 'ca', '2026-04-01 02:32:40+00'),
    ('69cccb715ef0d23df61a59e2', '699e5bada584c10c16b6ece2', 'UMAIR', 'FAISAL', '3068816706', '1991-05-13', '673190955', '0955', '821513702RT0001', 'acct_1THJM2JyMZUxC7d1', '5058268', '003', '07758', '{"line1": "128 Deborah Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 2W8"}'::jsonb, 'ca', '2026-04-01 07:38:16+00'),
    ('69cd5baef16fd8491960f865', '69a4eed7a7a37f75bcec600e', 'Muhammad', 'Husain', '3064441444', '1984-10-11', '685419293', '9293', '775081011RT0001', 'acct_1THSxbF1iL9ddFiS', '5172200', '003', '07488', '{"line1": "906 F Duchess Street #404", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 6K3"}'::jsonb, 'ca', '2026-04-01 17:53:44+00'),
    ('69cec9c6428444f01e9f9e60', '69c62c01ed80440946099629', 'Jahan', 'Sarwar', '3062202246', '1989-06-29', '696768860', '8860', '728351024RT0001', 'acct_1THrLDFNTYXRFgjL', '6271890', '004', '76668', '{"line1": "522 Cornish Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0Z4"}'::jsonb, 'ca', '2026-04-02 19:55:44+00'),
    ('69cfe88e428444f01ea77d34', '699b39fda584c10c16b4fa5d', 'Sulman', 'Mahmood', '3068810165', '1973-02-22', '546969882', '9882', '733841936RT0001', 'acct_1TIARMJutv4SQ5o7', '2489924962', '828', '30052', '{"line1": "3240 33rd Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6S9"}'::jsonb, 'ca', '2026-04-03 16:19:21+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('69d00e1ded8184ce6c4e384c', '69a50f75a7a37f75bcec86b7', 'Mohammad', 'Talib', '3067167794', '1967-04-01', '680598620', '8620', '706315488RT0001', 'acct_1TICwQJxLzhTkM9V', '5665639', '010', '01018', '{"line1": "902 Childers Court", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6T9"}'::jsonb, 'ca', '2026-04-03 18:59:33+00'),
    ('69d303eff2cfa0c9f3cf0a0c', '69a3a0eea7a37f75bceb02fb', 'Nadeem', 'Tahir', '6395251515', '1961-06-08', '571214212', '4212', '71587 3543 RT0001', 'acct_1TP00HFTBDs1sLx1', '6201167', '004', '76668', '{"line1": "210 Rajput Way", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0V7"}'::jsonb, 'ca', '2026-04-06 00:52:56+00'),
    ('69d58928020c9af4469e680c', '69bc4dd35fc1dc6d1e30efa6', 'Muhammad Tufail', 'Shaikh Qureshi', '3062627290', '1964-12-15', '289188773', '8773', '831634274RT0001', 'acct_1TJiNdFbxVE42Syp', '0107824', '002', '00588', '{"line1": "906 Ledingham Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0B6"}'::jsonb, 'ca', '2026-04-07 22:45:50+00'),
    ('69d5897b020c9af4469e6ed8', '6993a9b3a584c10c16b14bd0', 'Ruperto', 'Montaos', '3064910816', '1980-12-04', '685477556', '7556', '746580935 RT0001', 'acct_1TJiOx2XafWkLWlI', '6248586', '010', '00818', '{"line1": "7th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0Z3"}'::jsonb, 'ca', '2026-04-07 22:47:12+00'),
    ('69d5b364020c9af446a036fb', '69d3e56b1dedbbfdc2ada93c', 'Muhammad', 'Ghani', '3063410786', '1976-06-15', '689433621', '3621', '764945663RT0001', 'acct_1TJlC0FWF5NhRemE', '6144542', '004', '77348', '{"line1": "103 Wakaw Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 4C9"}'::jsonb, 'ca', '2026-04-08 01:46:01+00'),
    ('69d5c23b020c9af446a07da9', '69a12484307b6864a0ae26d3', 'Canice', 'Chukwudi', '3067151073', '1979-12-12', '687847590', '7590', '725116347RT0001', 'acct_1TJmBIFDy3Ev5xZi', '6276140', '004', '76668', '{"line1": "254 Aniskotaw manor", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 1L9"}'::jsonb, 'ca', '2026-04-08 02:49:23+00'),
    ('69d5c2d8020c9af446a082e2', '69d19279f2cfa0c9f3c6a399', 'Ajaypal', 'Singh', '6398962525', '1996-06-15', '694689738', '9738', '70801 9369 RT0001', 'acct_1TJmDpFFqh5ixjDD', '6161811', '004', '77348', '{"line1": "815 Wilson Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 2M3"}'::jsonb, 'ca', '2026-04-08 02:52:00+00'),
    ('69d714f8020c9af446a7d884', '69cdd9d3f16fd8491966efc3', 'Muhammad', 'Ahmad', '3067167706', '1969-11-02', '673191052', '1052', '776672107RT0001', 'acct_1TK8jxFSajWnnENK', '5743397', '010', '01018', '{"line1": "454 Flynn Lane", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0L8"}'::jsonb, 'ca', '2026-04-09 02:54:40+00'),
    ('69d80951293b6c77307bcfd1', '69d4812c1dedbbfdc2b17ef8', 'Mohammad', 'Farid', '3063718690', '1977-05-02', '692687999', '7999', '77377 3767 RT001', 'acct_1TKP0s2X3GKHVV1K', '5117403', '003', '00010', '{"line1": "Coy Avenue", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M"}'::jsonb, 'ca', '2026-04-09 20:17:08+00'),
    ('69d83b18293b6c77307d7927', '69d42a5d1dedbbfdc2afa816', 'Hemendra Nath', 'Roy', '3069992021', '1989-07-15', '696986041', '6041', '728132234RT0001', 'acct_1TKSKPF0HHrnQyxv', '5366463', '003', '00008', '{"line1": "410 Wakabayashi Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 7L9"}'::jsonb, 'ca', '2026-04-09 23:49:33+00'),
    ('69db4924fb4bbf76b8dcf4bd', '6994ec29a584c10c16b1df7e', 'Amanpreet', 'singh', '6395250635', '1986-09-17', '692660061', '0061', '760413229RT0001', 'acct_1TLIPVJuy2hx3G6C', '5289202', '003', '07758', '{"line1": "5th Avenue North", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K"}'::jsonb, 'ca', '2026-04-12 07:26:20+00'),
    ('69ded6a5303513ffe228d805', '69decffa303513ffe228a29d', 'MD', 'KHAIRUZZAMAN', '3066405327', '1985-06-16', '689536431', '6431', '785845835RT0001', 'acct_1TMGysJwcXPmyxk4', '8088934', '010', '00418', '{"line1": "135 Ashworth Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0N1"}'::jsonb, 'ca', '2026-04-15 00:06:50+00'),
    ('69e2684f303513ffe2478acb', '6986d7604de23d6004a2914f', 'muhammad arslan', 'jafar', '3058000304', '1989-04-20', '675631683', '1683', '793857228RT0001', 'acct_1TNFpPFF21WucQi0', '7100434', '010', '05008', '{"line1": "467 Henick Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R 0J4"}'::jsonb, 'ca', '2026-04-17 17:05:10+00'),
    ('69e292fe303513ffe248d489', '699b975fa584c10c16b547fc', 'Rajveer', 'singh', '2362348387', '1997-01-19', '955728027', '8027', '735014631RT0001', 'acct_1TNIfeF3g29Kiubg', '1785788', '002', '11650', '{"line1": "715 Bolstad Turn", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0Y2"}'::jsonb, 'ca', '2026-04-17 20:07:14+00'),
    ('69e2a102303513ffe2492c87', '69dfed66303513ffe2312faa', 'chintan', 'patel', '6394703381', '1992-11-07', '691959266', '9266', '705467363RT0001', 'acct_1TNJbXF9unWG7uOy', '8014086', '010', '00418', '{"line1": "2825 Meadows Parkway", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0Y3"}'::jsonb, 'ca', '2026-04-17 21:07:04+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('69e2a464303513ffe2494ac6', '69b85e70fb14d015b766b948', 'Awanish', 'Singh', '7059945477', '1993-07-01', '694892811', '2811', '704736966RT0001', 'acct_1TNJpVFJr4B1wzWc', '6348707', '004', '37442', '{"line1": "3001 7th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 1B2"}'::jsonb, 'ca', '2026-04-17 21:21:31+00'),
    ('69e6bedd5a177309ea55516a', '69e52a01303513ffe26d17a7', 'CHAUDHRY ASIF', 'ALI', '3067176319', '1974-01-31', '576179048', '9048', '80626 0394 RT0001', 'acct_1TORmvF8DlJuVZLF', '0986585', '002', '30718', '{"line1": "934 Hunter Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0E6"}'::jsonb, 'ca', '2026-04-21 00:03:30+00'),
    ('69e854f2468f2ceebff35c36', '699b6853a584c10c16b526e4', 'Zeeshan', 'Bajwa', '3067131828', '1973-09-04', '697387512', '7512', '775106164RT0001', 'acct_1TOspfF1jdxlIUzJ', '5110945', '003', '01430', '{"line1": "275 Flynn Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0L8"}'::jsonb, 'ca', '2026-04-22 04:56:10+00'),
    ('69ea21d0468f2ceebf01bbb4', '69a88563a7a37f75bcf05d27', 'Samuel', 'Ayenigba', '6395254618', '1981-04-16', '694372632', '2632', '716538160RT0001', 'acct_1TPNWaFGMUNrH0Gk', '3928810', '001', '05578', '{"line1": "939 Kolynchuk Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0V8"}'::jsonb, 'ca', '2026-04-23 13:42:31+00'),
    ('69ea3407468f2ceebf025c62', '69e22285303513ffe244d9a6', 'viral', 'patel', '6397606279', '1991-09-02', '693851743', '1743', '740493630RT0001', 'acct_1TPOjo2UNR60jsjd', '1432125', '002', '10058', '{"line1": "209 Willis Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0L8"}'::jsonb, 'ca', '2026-04-23 15:00:09+00'),
    ('69eb9973468f2ceebf11888c', '69e4384a303513ffe25f47e5', 'Ajiro', 'Oboghor', '3435438587', '1992-10-28', '156025587', '5587', '72138 1366 RT0001', 'acct_1TPmXcFMyDDM9BMe', '1652028', '002', '50476', '{"line1": "2725 Meadows Parkway", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 1T7"}'::jsonb, 'ca', '2026-04-24 16:25:14+00'),
    ('69efc98d468f2ceebf3244d4', '69e12510303513ffe23c176a', 'Nighil', 'Mathew', '5197163736', '1983-04-27', '150887651', '7651', '767832629RT0001', 'acct_1TQvwMFW6SpQh3ke', '5042957', '003', '00982', '{"line1": "631 Beckett Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 4W6"}'::jsonb, 'ca', '2026-04-27 20:39:33+00'),
    ('69f106a1468f2ceebf3db222', '69c4cda1ed8044094605f189', 'NITIN', 'BATRA', '6393846556', '1974-03-21', '160750964', '0964', '785308776RT0001', 'acct_1TRH3ZF47up3jDmT', '6550339', '010', '08409', '{"line1": "451 Paton Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0B8"}'::jsonb, 'ca', '2026-04-28 19:12:25+00'),
    ('69f18c02468f2ceebf43564e', '69f1337b468f2ceebf3fc020', 'prabhdeep', 'Jassal', '3064913239', '1984-03-18', '680168200', '8200', '775206683RT0001', 'acct_1TRPwIFWpnjBmB3f', '6186575', '004', '76668', '{"line1": "604 Feheregyhazi Boulevard", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 1C9"}'::jsonb, 'ca', '2026-04-29 04:41:29+00'),
    ('69f24845468f2ceebf4984f6', '69ec2a51468f2ceebf16fa8d', 'Fatha', 'Ahmed', '3067134192', '1989-08-10', '684194624', '4624', '747220119RT0001', 'acct_1TRcTdFD7FywtV5p', '6479758', '004', '15442', '{"line1": "815 Wilson Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7J 2M3"}'::jsonb, 'ca', '2026-04-29 18:04:41+00'),
    ('69f28de7468f2ceebf4d7330', '69f0ef4e468f2ceebf3c88f4', 'Patrick', 'Findlator', '3065142163', '1985-12-02', '683739270', '9270', '756775672RT0001', 'acct_1TRh7AF1SIETBdKE', '3929823', '001', '00348', '{"line1": "110 Akhtar Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0Y9"}'::jsonb, 'ca', '2026-04-29 23:01:51+00'),
    ('69f2984c468f2ceebf4e51d5', '69eb25db468f2ceebf0fd4c0', 'Alexander', 'Gavu', '3062215268', '1989-06-04', '950503805', '3805', '729817031RT0001', 'acct_1TRho52WXfdIyW52', '27761592', '703', '00001', '{"line1": "630 8 Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0R2"}'::jsonb, 'ca', '2026-04-29 23:46:12+00'),
    ('69f2a91d468f2ceebf4f4b82', '69f2a47e468f2ceebf4f1a05', 'Biraj', 'Thapa', '6728335855', '2008-04-29', '959629833', '9833', '748191038RT0001', 'acct_1TRivU2YFYS5DO31', '7382294', '010', '00600', '{"line1": "131 Stromberg Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 7C4"}'::jsonb, 'ca', '2026-04-30 00:57:54+00'),
    ('69f2a920468f2ceebf4f4bc8', '69f2a47e468f2ceebf4f1a05', 'Biraj', 'Thapa', '6728335855', '2008-04-29', '959629833', '9833', '748191038RT0001', 'acct_1TRivZJzPqOnshAH', '7382294', '010', '00600', '{"line1": "131 Stromberg Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 7C4"}'::jsonb, 'ca', '2026-04-30 00:57:59+00'),
    ('69f3f979468f2ceebf5c04bb', '69c6ea03ed804409460ba494', 'Harjap singh', 'Samra', '6477163004', '1996-01-19', '962520953', '0953', '713063824RT0001', 'acct_1TS5KLF0J7xhDCBU', '6347983', '010', '01018', '{"line1": "343 Ells Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6K6"}'::jsonb, 'ca', '2026-05-01 00:53:05+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('69f4ba2f173f9129700f6d74', '69a81e13a7a37f75bceffe2d', 'Sachin', 'patel', '6397461189', '1989-11-11', '150113348', '3348', '754144418RT0001', 'acct_1TSIA3JzrGf4ET4A', '5096433', '003', '07418', '{"line1": "216 Akhtar Bend", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0B8"}'::jsonb, 'ca', '2026-05-01 14:35:17+00'),
    ('69f61fa4173f912970200f46', '69efb5a8468f2ceebf31389f', 'Amjad Ali', 'Shah', '3068506974', '1965-07-01', '680277084', '7084', '712327089RT0001', 'acct_1TSfznJyocmi4xVp', '6659024', '004', '77368', '{"line1": "227 Padget Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0H4"}'::jsonb, 'ca', '2026-05-02 16:00:26+00'),
    ('69f68baf173f912970277992', '699b27b5a584c10c16b4ee36', 'Alimul Haque', 'Khan', '6394702886', '1985-11-15', '693743957', '3957', '733923031RT0001', 'acct_1TSnSyF8YXkM7HD8', '5100383', '003', '07488', '{"line1": "350 Fairmont Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 4P1"}'::jsonb, 'ca', '2026-05-02 23:41:24+00'),
    ('69f6dd5a173f9129702bb751', '69afb6d8a7a37f75bcfa3498', 'Shahrukh', 'Nizami', '6395257540', '1994-01-04', '699882676', '2676', '000000000000000', 'acct_1TSsbJ2VrmMZDfdM', '6213017', '004', '76668', '{"line1": "146 Pezer Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7S 1J6"}'::jsonb, 'ca', '2026-05-03 05:29:51+00'),
    ('69f7a98d173f9129703375a3', '6995e970a584c10c16b24d11', 'Mahesh', 'Jadhav', '6395257060', '1986-09-12', '152915088', '5088', '729332965RT0001', 'acct_1TT6CUFL571SAkHH', '27180355', '703', '00001', '{"line1": "Summers Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 3W4"}'::jsonb, 'ca', '2026-05-03 20:01:08+00'),
    ('69f8f104173f9129703f9b7f', '69f7bbeb173f9129703466a4', 'Muhammad', 'Uddin', '3062614553', '1973-10-27', '676078868', '8868', '76107 7486 RT0001', 'acct_1TTS0a2ZupSpZU3X', '6054993', '004', '77348', '{"line1": "1135 Willowgrove Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 1J4"}'::jsonb, 'ca', '2026-05-04 19:18:15+00'),
    ('69fcd431173f912970652716', '69f2a201468f2ceebf4f01ba', 'Md Belayet', 'Hossain', '3068808885', '1976-01-01', '674376637', '6637', '764945663 RT0001', 'acct_1TUWHhFMUZXKmv2k', '0009385', '002', '50328', '{"line1": "1119 Steeves Avenue", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 7N3"}'::jsonb, 'ca', '2026-05-07 18:04:21+00'),
    ('69fcfd67173f91297068df36', '69fcf390173f912970683a18', 'MUHAMMAD', 'TARIQ', '3067443005', '1969-09-01', '676102312', '2312', '777987959RT0001', 'acct_1TUZ1rFX8KvCovp6', '5193669', '003', '07488', '{"line1": "Labine View", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6N2"}'::jsonb, 'ca', '2026-05-07 21:00:13+00'),
    ('69fd082b173f912970699d66', '69f4f13d173f91297012577d', 'Harmeet', 'Singh', '3062613984', '1982-10-13', '693293615', '3615', '710309766RT0001', 'acct_1TUZkKFBlvoyXzuK', '6144937', '004', '77238', '{"line1": "2315 McClocklin Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R 0K7"}'::jsonb, 'ca', '2026-05-07 21:46:08+00'),
    ('69fd0a4d173f91297069c265', '69f221c1468f2ceebf46bd92', 'surya', 'sapkota', '2368187796', '1990-01-05', '693779738', '9738', '759456353RT0001', 'acct_1TUZt6Ju0QM2tTdr', '6197492', '004', '77348', '{"line1": "Rosewood Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0R9"}'::jsonb, 'ca', '2026-05-07 21:55:17+00'),
    ('69fe4743173f912970767ae4', '69f20a53468f2ceebf462b5c', 'malkeet singh', 'palyia', '6397420015', '1998-04-25', '691103055', '3055', '751108937RT0001', 'acct_1TUuzsFZrxVZ4Urv', '1152181', '002', '60152', '{"line1": "202 McKague Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7R 0L8"}'::jsonb, 'ca', '2026-05-08 20:27:35+00'),
    ('69fec66b173f9129707f210b', '699df992a584c10c16b6a987', 'Naveed', 'Bhutta', '3068800790', '1981-05-29', '746917921', '7921', '828227009RT0001', 'acct_1TV3SuFItoRHPGEY', '6021769', '004', '77348', '{"line1": "182 Rita Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 2N6"}'::jsonb, 'ca', '2026-05-09 05:30:10+00'),
    ('69ffd680173f912970890362', '69a6c8baa7a37f75bceeb19f', 'Jackson', 'Obazee', '3062412067', '1969-04-03', '152486569', '6569', '71699 0833 RT0001', 'acct_1TgX4gFCEbHc2TDd', '6240534', '010', '01018', '{"line1": "118 Carter Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 7K3"}'::jsonb, 'ca', '2026-05-10 00:51:06+00'),
    ('6a03d41a173f912970ae5da0', '6a0384b0173f912970aa1167', 'sunil kumar', 'Khullar', '5197743821', '1995-08-20', '953908605', '8605', '750311748RT0001', 'acct_1TWRcYFPg8J9uJuz', '5289616', '003', '00522', '{"line1": "527 Meadows Boulevard", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0G1"}'::jsonb, 'ca', '2026-05-13 01:29:52+00'),
    ('6a04c290173f912970b53780', '698826634de23d6004a2b07a', 'Tapash', 'Rozario', '3062620993', '1986-08-12', '688948736', '8736', '79673 4960 RT000', 'acct_1TWhVB2Y0ZG8dp5M', '5357837', '010', '00507', '{"line1": "443 Avenue Q North", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 2X9"}'::jsonb, 'ca', '2026-05-13 18:27:21+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('6a04e08b173f912970b67a16', '69fffc23173f9129708a51a5', 'Jaydev', 'Mori', '3062030944', '1988-09-22', '686338328', '8328', '724438205RT0001', 'acct_1TWjUyFB4XMAswkv', '8065438', '010', '00418', '{"line1": "978 Pringle Cove", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0V3"}'::jsonb, 'ca', '2026-05-13 20:35:16+00'),
    ('6a054487173f912970bd1646', '6a00c737173f91297091be33', 'MD Sirajul', 'Islam', '3068503687', '1971-07-21', '692552060', '2060', '704966167RT0001', 'acct_1TWq9nFQln6nN8yH', '8874581', '010', '00518', '{"line1": "307 Eaton Lane", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0H3"}'::jsonb, 'ca', '2026-05-14 03:41:46+00'),
    ('6a09deec173f912970f57856', '6a08ce2e173f912970ec9474', 'Akhtar', 'Abbas', '3067166602', '1977-11-27', '670262831', '2831', '753186873 RT0001', 'acct_1TY6dRJx7oFGcKjJ', '5138623', '003', '07408', '{"line1": "126 Sharma Lane", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 1L1"}'::jsonb, 'ca', '2026-05-17 15:29:39+00'),
    ('6a0a2564173f912970f83f5b', '69f26947468f2ceebf4afeed', 'Md Ayoub', 'Ali', '3063781336', '1995-01-02', '687851105', '1105', '743865628RT0001', 'acct_1TYBKPFIrZtN9GBn', '6682819', '004', '00308', '{"line1": "220 Clarence Avenue South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 1H3"}'::jsonb, 'ca', '2026-05-17 20:30:13+00'),
    ('6a0a2575173f912970f83f87', '69f26947468f2ceebf4afeed', 'Md Ayoub', 'Ali', '3063781336', '1995-01-02', '687851105', '1105', '743865628RT0001', 'acct_1TYBKfF8Ri8e1O8s', '6682819', '004', '00308', '{"line1": "220 Clarence Avenue South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 1H3"}'::jsonb, 'ca', '2026-05-17 20:30:25+00'),
    ('6a0b598a173f91297008e0f2', '6a0657f8173f912970d091a7', 'Birkaran', 'Singh', '3068800988', '1995-02-08', '695941161', '1161', '729384560RT0001', 'acct_1TYVqk2Wr6ndQ55X', '1303325', '002', '50328', '{"line1": "906 C Duchess Street", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 6K3"}'::jsonb, 'ca', '2026-05-18 18:25:03+00'),
    ('6a0b598a173f91297008e105', '6a0657f8173f912970d091a7', 'Birkaran', 'Singh', '3068800988', '1995-02-08', '695941161', '1161', '729384560RT0001', 'acct_1TYVqlJwRnyzULir', '1303325', '002', '50328', '{"line1": "906 C Duchess Street", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 6K3"}'::jsonb, 'ca', '2026-05-18 18:25:01+00'),
    ('6a0e0840173f91297029c1aa', '69ea3e01468f2ceebf02cab6', 'Mohammed', 'Sarker', '6475350758', '1987-08-24', '967593534', '3534', '722600426RT0001', 'acct_1TZFaC2Yx9kv87n3', '5395801', '003', '07748', '{"line1": "19 Barr Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 4G1"}'::jsonb, 'ca', '2026-05-20 19:15:01+00'),
    ('6a0e0846173f91297029cb2a', '69ea3e01468f2ceebf02cab6', 'Mohammed', 'Sarker', '6475350758', '1987-08-24', '967593534', '3534', '722600426RT0001', 'acct_1TZFaI2Z5heZAxGy', '5395801', '003', '07748', '{"line1": "19 Barr Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 4G1"}'::jsonb, 'ca', '2026-05-20 19:15:08+00'),
    ('6a0fec40d25e82c30a5ffea5', '6a0fd829173f9129703ea040', 'Manmeetpal', 'Singh', '4386807120', '1997-09-06', '960085934', '5934', '732623624RT0001', 'acct_1TZloeFNWJKtAUQs', '1787322', '002', '00901', '{"line1": "278 Pinehouse Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 4W9"}'::jsonb, 'ca', '2026-05-22 05:40:06+00'),
    ('6a10c59bbaf4f7d5a7539557', '6a03ea7f173f912970af8334', 'Karampreet Singh', 'Gurm', '3066206338', '1999-05-03', '695352567', '2567', '71430 7030 RT0001', 'acct_1Ta0I8JvEtYmMUE2', '0116341447', '010', '30800', '{"line1": "541 5th Avenue North", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 2R1"}'::jsonb, 'ca', '2026-05-22 21:07:32+00'),
    ('6a11b498baf4f7d5a75d1fac', '69ea50b9468f2ceebf036bdc', 'Rostam', 'Alawis', '6393180264', '1971-02-01', '650587934', '7934', '868765074RT0001', 'acct_1TaGCvJuxtd9vaNK', '6685125', '004', '00308', '{"line1": "138 Banyan Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 1H8"}'::jsonb, 'ca', '2026-05-23 14:07:13+00'),
    ('6a162ac0baf4f7d5a786567c', '6a0a8387173f912970fcee2f', 'Mir Mehadi Bin Shamsher', 'Ali', '3063611543', '1986-09-20', '681973327', '3327', '751765827RC0001', 'acct_1TbUGuJuVpPOddbL', '5056023', '003', '07388', '{"line1": "222 Fairmont Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 4P5"}'::jsonb, 'ca', '2026-05-26 23:20:23+00'),
    ('6a1cfcafbaf4f7d5a7b5e3d3', '6a061d75173f912970cca09a', 'Christianne Ghiljenn', 'pascual', '4747746423', '1989-08-14', '672165305', '5305', '776583577RT0001', 'acct_1TdMXvFEQYTrrkh3', '6429432', '010', '01018', '{"line1": "2610 Richardson Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 4C5"}'::jsonb, 'ca', '2026-06-01 03:29:40+00'),
    ('6a20be4618271493aef45bf7', '6a0250dd173f9129709ed3eb', 'Satinder', 'Singh', '9059243818', '1996-12-09', '779140938', '0938', '753936756RT0001', 'acct_1TeOaNJx5nqqmVxz', '4074011', '320', '02002', '{"line1": "339 Avenue Q South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 2Y2"}'::jsonb, 'ca', '2026-06-03 23:52:31+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('6a20c0ad18271493aef47d62', '6a1dcc01baf4f7d5a7b95e45', 'Muhammad Asad', 'Warraich', '3062507073', '1973-01-06', '696760495', '0495', '70019 1158 RT0001', 'acct_1TeOkIF0ODmYgbgK', '5336239', '010', '00518', '{"line1": "Trimble Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W0L8"}'::jsonb, 'ca', '2026-06-04 00:02:47+00'),
    ('6a275d25469d97b44a09092e', '6a090541173f912970ee1cbd', 'Manjeet', 'Singh', '7788902191', '1999-09-23', '954484002', '4002', '776135170RT0001', 'acct_1TgDSXF9oMBmPqIT', '0980021', '002', '70680', '{"line1": "406 Nelson Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7S 1N5"}'::jsonb, 'ca', '2026-06-09 00:23:56+00'),
    ('6a2847c0ca6c0d82be6348c6', '6a0e71a8173f9129702ee7f9', 'AASHISH', 'viswakarma', '3062034075', '1991-08-16', '673869251', '9251', '769088022RT0001', 'acct_1TgT5FFEqdQqt5c8', '3895712', '001', '26808', '{"line1": "11 O''Neil Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 1W7"}'::jsonb, 'ca', '2026-06-09 17:04:57+00'),
    ('6a2847c7ca6c0d82be6348f9', '6a0e71a8173f9129702ee7f9', 'AASHISH', 'viswakarma', '3062034075', '1991-08-16', '673869251', '9251', '769088022RT0001', 'acct_1TgT5L2YTouhyhy8', '3895712', '001', '26808', '{"line1": "11 O''Neil Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 1W7"}'::jsonb, 'ca', '2026-06-09 17:05:04+00'),
    ('6a2be3f0ca6c0d82be7b27f0', '69a8f304a7a37f75bcf15e56', 'Yared', 'Abraha', '3062510071', '1984-10-30', '590203626', '3626', '732293907RT0001', 'acct_1ToOV92UnyBVBOP4', '1208225', '002', '00018', '{"line1": "904 Temperance Street", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 0N4"}'::jsonb, 'ca', '2026-06-12 10:48:07+00'),
    ('6a2c7af7ca6c0d82be7ea739', '6a2c75dbca6c0d82be7e9877', 'Efemona', 'Avre', '6393176138', '1986-09-10', '151771391', '1391', '708867833RT0001', 'acct_1ThcgpFUzy6qPyDh', '1261126', '002', '50328', '{"line1": "222 Streb Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 4V4"}'::jsonb, 'ca', '2026-06-12 21:32:30+00'),
    ('6a2f571bca6c0d82be92a711', '6a0cac89173f9129701ce9e2', 'adetokunbo', 'Adeyemi', '6399942263', '1973-06-13', '693102931', '2931', '734939146RC0001', 'acct_1TiPRsFGuPzvMmEw', '1010958', '003', '07418', '{"line1": "315 Dickson Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0Z1"}'::jsonb, 'ca', '2026-06-15 01:36:19+00'),
    ('6a2f8025ca6c0d82be93efa3', '6a2f5af8ca6c0d82be92be4a', 'Md Ismail', 'Hossain', '3062614950', '1989-01-01', '674492400', '2400', '790321079RT0001', 'acct_1TiSBKFLpQMBMaTt', '5460239', '010', '00018', '{"line1": "192 Saint Paul''s Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 0H1"}'::jsonb, 'ca', '2026-06-15 04:31:25+00'),
    ('6a308cb5ca6c0d82be9be49a', '6a03b766173f912970acb7da', 'Jaskarn', 'Singh', '6395336164', '2008-06-15', '699497707', '7707', '699497707RT0001', 'acct_1Tik4EFU74quQ6jN', '1428381', '002', '10058', '{"line1": "2535 Meadows Parkway", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 1W7"}'::jsonb, 'ca', '2026-06-15 23:37:16+00'),
    ('6a30bfc8ca6c0d82be9d5de1', '6a30927fca6c0d82be9c3166', 'Hasan', 'Qasim', '6394711805', '1996-05-26', '671284305', '4305', '784594079RT0001', 'acct_1TinT6FSZ5mer8IJ', '5528038', '010', '01018', '{"line1": "818 Kensington Boulevard", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6H1"}'::jsonb, 'ca', '2026-06-16 03:15:10+00'),
    ('6a337ae0ca6c0d82beb25e43', '6a28d605ca6c0d82be67b2a3', 'Manesh', 'Thomas', '3068509642', '1980-08-02', '685614349', '4349', '767718356RT0001', 'acct_1TjY6kFKeBHSsVFe', '1984420', '001', '26808', '{"line1": "535 Reid Way", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 3J4"}'::jsonb, 'ca', '2026-06-18 04:58:01+00'),
    ('6a3736f0ca6c0d82bed153dc', '6a37186fca6c0d82bed080f6', 'Aamir', 'Mehmood', '3067158786', '1970-12-29', '674464193', '4193', '779920073RT0001', 'acct_1TkZhJFD57auDaL7', '6485178', '004', '77408', '{"line1": "Pringle Lane", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T"}'::jsonb, 'ca', '2026-06-21 00:57:11+00'),
    ('6a39bdd1ca6c0d82bee105db', '6a338e3aca6c0d82beb2ec54', 'gaurangkumar', 'pandya', '3063027574', '1981-05-29', '688154939', '4939', '715952834RT0001', 'acct_1TlGmIF3VmJltGR0', '5229877', '003', '07418', '{"line1": "186 Pinehouse Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 4T4"}'::jsonb, 'ca', '2026-06-22 22:57:12+00'),
    ('6a409b37ca6c0d82be0f9d29', '69c8cbe1ed8044094610e759', 'Paolo', 'Osias', '6394714791', '1981-03-24', '683500000', '0000', '000000725788830', 'acct_1Tn9s4F8YpMBga1g', '41580283', '703', '00001', '{"line1": "2001 7th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0Z7"}'::jsonb, 'ca', '2026-06-28 03:55:29+00'),
    ('6a45c452ca6c0d82be37bf45', '6a3f41abca6c0d82be04abd7', 'OPEYEMI', 'AKINLUYI', '3063719217', '1989-09-16', '696405570', '5570', '707659231 RT0001', 'acct_1ToZnXFUsX7qEHRJ', '5089198', '003', '07758', '{"line1": "243 Herold Terrace", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 1J6"}'::jsonb, 'ca', '2026-07-02 01:52:10+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('6a4694a6ca6c0d82be3e1c22', '6a45bcc1ca6c0d82be3758db', 'M A', 'Basar', '6395351104', '2002-05-10', '699680526', '0526', '790207377RT0001', 'acct_1TouCyF8V6iT4Fr1', '5193636', '003', '07758', '{"line1": "104 Lindsay Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 4B4"}'::jsonb, 'ca', '2026-07-02 16:41:00+00'),
    ('6a4c1863ca6c0d82be661dbd', '6a49d82aca6c0d82be5939ea', 'Miren', 'Jadav', '2269898200', '2008-07-06', '964357032', '7032', '70282 5837 RT0001', 'acct_1TqJgoFXaiLtDYfa', '7930376', '320', '02002', '{"line1": "434B McArthur Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6Y3"}'::jsonb, 'ca', '2026-07-06 21:04:24+00'),
    ('6a4ee256ca6c0d82be74d8a5', '6a473ca4ca6c0d82be430118', 'Dharampreet', 'singh', '6478792954', '2000-09-10', '954128070', '8070', '78852 5830 RT000', 'acct_1Tr5EkFGfwrzW7wB', '2539926', '002', '67082', '{"line1": "2617 7th Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 2X2"}'::jsonb, 'ca', '2026-07-08 23:50:37+00'),
    ('6a4ef116ca6c0d82be759337', '6a367946ca6c0d82becd5f6f', 'md bodrul', 'hussain', '4372551549', '1999-10-22', '956824288', '4288', '764945663RC0001', 'acct_1Tr6DfFBtqbAuDs6', '6266282', '010', '07832', '{"line1": "108 Davidson Crescent", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 4A1"}'::jsonb, 'ca', '2026-07-09 00:53:36+00'),
    ('6a4ef29cca6c0d82be75a294', '6a47e745ca6c0d82be4999a7', 'Brandon', 'Crone', '3063801676', '1986-11-17', '648773463', '3463', '800352361RT0001', 'acct_1Tr6JxF5CNekSfRm', '6188942', '004', '76668', '{"line1": "434 Bolton Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 0H1"}'::jsonb, 'ca', '2026-07-09 01:00:05+00'),
    ('6a4f0e9bca6c0d82be76fc84', '6a4db6c5ca6c0d82be6f274f', 'Manas', 'Abraham Mathew', '3067170955', '1989-02-08', '687169912', '9912', '727245409RT0001', 'acct_1Tr8BYJt6mtTe39p', '6880009', '004', '00308', '{"line1": "402 Avenue P South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 2W6"}'::jsonb, 'ca', '2026-07-09 02:59:31+00'),
    ('6a4f2351ca6c0d82be77d526', '69fdfea4173f912970736b52', 'Md zafar', 'Mia', '4372555601', '1974-10-19', '961048469', '8469', '961048469191074', 'acct_1Tr9Z4FVwlCzMwyb', '6111294', '004', '00322', '{"line1": "208 Avenue V South", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 3E3"}'::jsonb, 'ca', '2026-07-09 04:27:56+00'),
    ('6a4fef3dca6c0d82be7ce274', '6a42fa85ca6c0d82be1f9443', 'Pruthviraj', 'Chaudhari', '3062000874', '1991-11-30', '690816525', '6525', 'RT0001710993015', 'acct_1TrN94FN32Kj2PSi', '6575696', '004', '60537', '{"line1": "451 Kloppenburg Street", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7W 0N8"}'::jsonb, 'ca', '2026-07-09 18:57:59+00'),
    ('6a5034e4ca6c0d82be7eb429', '699756c0a584c10c16b2fb37', 'Muhammad', 'Khan', '3069143618', '1998-08-03', '584310296', '0296', '731528949RT0001', 'acct_1TrRmf2VAvYwsPyQ', '1269224', '002', '20628', '{"line1": "203 109th Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7N 1R4"}'::jsonb, 'ca', '2026-07-09 23:55:06+00'),
    ('6a50d5e1ca6c0d82be832281', '6a45ead2ca6c0d82be39723e', 'Muhammad', 'kashif', '3069143424', '1980-02-27', '674766209', '6209', '782101620RT0001', 'acct_1TrcVO2YGr2LVTQ2', '5422884', '010', '01018', '{"line1": "210-19 Camponi Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 1J7"}'::jsonb, 'ca', '2026-07-10 11:22:00+00'),
    ('6a5329b2ca6c0d82be93b43e', '69a936eba7a37f75bcf19a65', 'Syed Usama', 'Saeed', '3067132693', '1995-04-20', '673724795', '4795', '736358359RT0001', 'acct_1TsGBWFHG901t8Z7', '5058896', '003', '07748', '{"line1": "209 A Cree Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 7Y9"}'::jsonb, 'ca', '2026-07-12 05:44:11+00'),
    ('6a5329b8ca6c0d82be93b461', '69a936eba7a37f75bcf19a65', 'Syed Usama', 'Saeed', '3067132693', '1995-04-20', '673724795', '4795', '736358359RT0001', 'acct_1TsGBcFIimk0dpm9', '5058896', '003', '07748', '{"line1": "209 A Cree Place", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 7Y9"}'::jsonb, 'ca', '2026-07-12 05:44:18+00'),
    ('6a550f5bca6c0d82be9d34da', '6a4eb108ca6c0d82be7328d8', 'Mali', 'Mahadhi', '3067172562', '1987-01-01', '659871065', '1065', '70906 1436 RT000', 'acct_1TsmWpJyLnx5wArW', '1142720', '002', '20628', '{"line1": "3305 14 Street East", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7H 0B5"}'::jsonb, 'ca', '2026-07-13 16:16:15+00'),
    ('6a596ca2ca6c0d82beb46f20', '69f7be14173f9129703482d8', 'Inderpreet', 'Singh', '4378385558', '2008-07-16', '964547863', '7863', '77582 7421 RT0001', 'acct_1Ttyw7FbBlDR7t9G', '0862185', '002', '25932', '{"line1": "742 Bentley Manor", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6P2"}'::jsonb, 'ca', '2026-07-16 23:43:21+00'),
    ('6a59864dca6c0d82beb50e3d', '6a3f3089ca6c0d82be03c597', 'lakhwinder', 'singh', '6478983575', '1995-09-05', '699882528', '2528', '78513 7811 RT0001', 'acct_1Tu0e5F4siLJOZrl', '2978180', '002', '77982', '{"line1": "1245 Kensington Manor", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 7P7"}'::jsonb, 'ca', '2026-07-17 01:32:52+00');

INSERT INTO driver_bank_import (old_id, old_driver_id, first_name, last_name, phone, date_of_birth, sin_raw, sin_last4, gst_bn, stripe_account_id, account_number, institute_number, transit_number, address, country, old_created_at)
VALUES
    ('6a598651ca6c0d82beb50e89', '6a3f3089ca6c0d82be03c597', 'lakhwinder', 'singh', '6478983575', '1995-09-05', '699882528', '2528', '78513 7811 RT0001', 'acct_1Tu0eAF07FYratmA', '2978180', '002', '77982', '{"line1": "1245 Kensington Manor", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 7P7"}'::jsonb, 'ca', '2026-07-17 01:32:58+00'),
    ('6a59a128ca6c0d82beb5d423', '6a599c7dca6c0d82beb5acf7', 'Oleksandr', 'Mykhailishyn', '6722003225', '1995-03-22', '964106363', '6363', '71086 1832 RT0001', 'acct_1Tu2QzFaMzoBKP4A', '5071477', '003', '04800', '{"line1": "170 Phelps Way", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7V 0K8"}'::jsonb, 'ca', '2026-07-17 03:27:30+00'),
    ('6a5c01d4ca6c0d82bec8295e', '69decce7303513ffe226c973', 'Jeremy', 'Harrison', '3062803774', '1979-09-07', '647546100', '6100', '700601099RT0002', 'acct_1TugyDFMOeKup0R5', '0089028', '002', '03459', '{"line1": "620 Cornish Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0Y3"}'::jsonb, 'ca', '2026-07-18 22:44:29+00'),
    ('6a5c01daca6c0d82bec8298a', '69decce7303513ffe226c973', 'Jeremy', 'Harrison', '3062803774', '1979-09-07', '647546100', '6100', '700601099RT0002', 'acct_1TugyI2Wu33SIpcu', '0089028', '002', '03459', '{"line1": "620 Cornish Road", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7T 0Y3"}'::jsonb, 'ca', '2026-07-18 22:44:36+00'),
    ('6a5c63beca6c0d82becc2837', '699e0788a584c10c16b6b4c1', 'simranjit', 'singh', '3064563020', '1996-06-11', '975060740', '0740', '742350168RT0001', 'acct_1TunUVFC7cDPcyE9', '5271002', '003', '07418', '{"line1": "180 Pinehouse Drive", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7K 4T4"}'::jsonb, 'ca', '2026-07-19 05:42:15+00'),
    ('6a5d680eca6c0d82bed2cf1d', '6a2426d0469d97b44af8496f', 'Tanjilur', 'Rahman', '3067131821', '1996-11-25', '692718414', '8414', '725604763RT0001', 'acct_1Tv4pLFG0BnWcvjR', '6765460', '004', '77368', '{"line1": "22 Street West", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7M 0T3"}'::jsonb, 'ca', '2026-07-20 00:12:56+00'),
    ('6a614d27ca6c0d82beea3a8a', '69e6b56e5a177309ea54e1ca', 'Julio', 'Castillo', '6393842189', '1983-07-27', '682536677', '6677', '788825438RT0001', 'acct_1Tw9EOFTyqhxMo4t', '7252803', '320', '02002', '{"line1": "239 Bentley Court", "city": "Saskatoon", "state": "Saskatchewan", "postal_code": "S7L 6L4"}'::jsonb, 'ca', '2026-07-22 23:07:12+00');

-- STEP 3: Match to existing drivers by phone number
UPDATE driver_bank_import bi
SET matched_user_id = u.id::uuid,
    migration_status = 'matched'
FROM users u
INNER JOIN drivers d ON d.user_id = u.id
WHERE u.phone = bi.phone
  AND bi.migration_status = 'pending';

-- STEP 3b: Try matching unmatched via the driver_csv_import table (old_id linkage)
-- (Only works if you already ran driver_csv_migration.sql)
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'driver_csv_import') THEN
    UPDATE driver_bank_import bi
    SET matched_user_id = dci.matched_driver_id,
        migration_status = 'matched'
    FROM driver_csv_import dci
    WHERE dci.old_id = bi.old_driver_id
      AND dci.matched_driver_id IS NOT NULL
      AND bi.migration_status = 'pending';
  END IF;
END $$;

-- STEP 4: Review matches before proceeding
SELECT migration_status, COUNT(*) FROM driver_bank_import GROUP BY migration_status;

-- Preview matched:
SELECT bi.first_name, bi.last_name, bi.phone,
       bi.date_of_birth, bi.sin_last4, bi.gst_bn, bi.stripe_account_id,
       bi.matched_user_id
FROM driver_bank_import bi
WHERE bi.migration_status = 'matched'
ORDER BY bi.first_name;

-- Preview unmatched (exist in old system but NOT in new):
SELECT bi.first_name, bi.last_name, bi.phone, bi.old_driver_id
FROM driver_bank_import bi
WHERE bi.migration_status = 'pending'
ORDER BY bi.first_name;

-- ============================================================
-- STEP 5: Encrypt SINs via Vault RPC
-- !!! ONLY RUN AFTER REVIEWING STEP 4 OUTPUT !!!
-- This encrypts each SIN into vault.secrets and stores the UUID.
-- ============================================================

UPDATE driver_bank_import
SET sin_vault_id = encrypt_driver_pii(sin_raw)
WHERE migration_status = 'matched'
  AND sin_raw IS NOT NULL
  AND sin_raw != '';

-- Verify encryption succeeded (sin_vault_id should be a UUID, not digits):
SELECT id, first_name, last_name, sin_last4,
       LEFT(sin_vault_id, 8) AS vault_prefix,
       LENGTH(sin_vault_id) AS vault_len
FROM driver_bank_import
WHERE migration_status = 'matched' AND sin_vault_id IS NOT NULL
LIMIT 5;

-- ============================================================
-- STEP 6: Apply updates to drivers table
-- !!! ONLY RUN AFTER VERIFYING STEP 5 ENCRYPTION !!!
-- ============================================================

-- 6a: Update SIN (encrypted), sin_last4, sin_collected_at
UPDATE drivers d
SET sin = bi.sin_vault_id,
    sin_last4 = bi.sin_last4,
    sin_collected_at = COALESCE(bi.old_created_at, NOW())
FROM driver_bank_import bi
WHERE d.user_id = bi.matched_user_id
  AND bi.migration_status = 'matched'
  AND bi.sin_vault_id IS NOT NULL
  AND d.sin IS NULL;  -- NULL-only fill: never overwrite existing SIN

-- 6b: Update date_of_birth
UPDATE drivers d
SET date_of_birth = bi.date_of_birth
FROM driver_bank_import bi
WHERE d.user_id = bi.matched_user_id
  AND bi.migration_status = 'matched'
  AND bi.date_of_birth IS NOT NULL
  AND d.date_of_birth IS NULL;  -- NULL-only fill

-- 6c: Update GST/HST business number (canonical column is gst_bn, NOT gst_hst_number)
UPDATE drivers d
SET gst_bn = bi.gst_bn,
    gst_registered = TRUE
FROM driver_bank_import bi
WHERE d.user_id = bi.matched_user_id
  AND bi.migration_status = 'matched'
  AND bi.gst_bn IS NOT NULL
  AND bi.gst_bn != ''
  AND d.gst_bn IS NULL;  -- NULL-only fill

-- 6d: Update Stripe Connect account ID
UPDATE drivers d
SET stripe_account_id = bi.stripe_account_id
FROM driver_bank_import bi
WHERE d.user_id = bi.matched_user_id
  AND bi.migration_status = 'matched'
  AND bi.stripe_account_id IS NOT NULL
  AND bi.stripe_account_id != ''
  AND d.stripe_account_id IS NULL;  -- NULL-only fill

-- Mark updated rows
UPDATE driver_bank_import
SET migration_status = 'updated'
WHERE migration_status = 'matched';

-- STEP 7: Final verification
SELECT migration_status, COUNT(*) FROM driver_bank_import GROUP BY migration_status;

SELECT d.user_id, bi.first_name, bi.last_name, bi.phone,
       d.sin_last4, d.sin_collected_at, d.date_of_birth,
       d.gst_bn, d.stripe_account_id
FROM drivers d
JOIN driver_bank_import bi ON d.user_id = bi.matched_user_id
WHERE bi.migration_status = 'updated'
ORDER BY bi.first_name;

-- ============================================================
-- STEP 8: CRITICAL — Purge plaintext SIN from staging table
-- Run this IMMEDIATELY after verification. Plaintext SINs must
-- not persist in any table. PIPEDA requires data minimization.
-- ============================================================

UPDATE driver_bank_import SET sin_raw = NULL;

-- STEP 9: Cleanup (run when fully satisfied)
-- DROP TABLE IF EXISTS driver_bank_import;