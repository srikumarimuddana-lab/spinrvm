export function maskEmail(email: string | null | undefined): string {
  if (!email) return "—";
  const [local, domain] = email.split("@");
  if (!domain) return "***";
  return `${local[0] ?? ""}***@${domain}`;
}

export function maskPhone(phone: string | null | undefined): string {
  if (!phone) return "—";
  const digits = phone.replace(/\D/g, "");
  if (digits.length < 4) return "***";
  return `***-${digits.slice(-4)}`;
}

export function maskPlate(plate: string | null | undefined): string {
  if (!plate) return "—";
  const clean = plate.replace(/\s/g, "");
  if (clean.length <= 3) return "***";
  return `***${clean.slice(-3)}`;
}

export function maskVin(vin: string | null | undefined): string {
  if (!vin) return "—";
  const clean = vin.replace(/\s/g, "");
  if (clean.length <= 4) return "***";
  return `***${clean.slice(-4)}`;
}
