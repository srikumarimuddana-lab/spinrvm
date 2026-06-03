# Spinr vehicle-type illustrations

White + red brand illustrations for the **Admin → Vehicle Types** picker
(rider-facing `image_url` / `illustration_url`). Uber-inspired in spirit —
clean, premium, instantly readable — but drawn from scratch, so nothing is
copied from any other app.

| File | Vehicle type | Suggested name | Seats |
|------|--------------|----------------|-------|
| `spinr-economy.png` | Compact 4-door sedan | Spinr Go (economy) | 4 |
| `spinr-premium.png` | Sleek executive sedan | Spinr Premium | 4 |
| `spinr-suv.png` | Raised 5-seat SUV | Spinr SUV | 5 |
| `spinr-van.png` | 6-seat passenger van | Spinr Van / XL | 6 |

- **Format:** PNG, 1000×560, transparent background (renders cleanly on the
  admin card's `object-contain` grey tile and in the rider app).
- **Size:** all < 60 KB — well under the 500 KB upload cap in
  `admin-dashboard/.../vehicle-types/page.tsx`.
- **Brand red:** `#EE2B2B` (matches the rider app).

## How to use

1. Admin Dashboard → **Vehicle Types**.
2. Create/Edit a type, then under **Illustration** upload the matching PNG
   (or paste a hosted URL).

## Regenerating / tweaking

Source is vector — edit `generate_vehicles.py` and re-run:

```bash
pip install cairosvg
python3 generate_vehicles.py   # rewrites every .svg and .png here
```

The `.svg` files are the editable masters; the `.png` files are what you
upload.
