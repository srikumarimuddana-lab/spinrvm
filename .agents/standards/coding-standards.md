---
name: Coding Standards
description: Code style and formatting standards for Python and TypeScript in the Spinr project
---

# Coding Standards

## Python (Backend)

### Style
- **Formatter**: Black (line length 120)
- **Linter**: Flake8 (line length 120)
- **Naming**: `snake_case` for functions and variables, `PascalCase` for classes
- **Imports**: Group in order — stdlib, third-party, local. Absolute imports preferred.

### Structure Rules
| Rule | Limit |
|------|-------|
| Max function length | 50 lines |
| Max file length | 300 lines |
| Max function arguments | 5 (use dataclass/dict beyond this) |
| Max nesting depth | 3 levels |

### Required Patterns
```python
# All functions must have type hints
def get_user(user_id: str) -> dict:

# All public functions must have docstrings
def calculate_fare(pickup: dict, dropoff: dict) -> dict:
    """Calculate ride fare based on distance and vehicle type."""

# Use f-strings for formatting (not .format() or %)
logger.info(f"Processing ride {ride_id}")

# Use pathlib for file paths (not os.path)
from pathlib import Path
config_path = Path("backend/config.yaml")
```

### Forbidden Patterns
```python
# ❌ No print() — use logger
print("Debug info")  # BAD
logger.info("Debug info")  # GOOD

# ❌ No bare except
except:  # BAD
except Exception as e:  # GOOD

# ❌ No mutable default arguments
def func(items=[]):  # BAD
def func(items=None):  # GOOD
    items = items or []

# ❌ No hardcoded secrets
JWT_SECRET = "my-secret"  # BAD
JWT_SECRET = os.environ.get("JWT_SECRET")  # GOOD
```

---

## TypeScript (Frontend)

### Style
- **Strict mode**: Enabled in `tsconfig.json`
- **Naming**: `camelCase` for functions/variables, `PascalCase` for components/types/interfaces
- **File naming**: `PascalCase.tsx` for components, `camelCase.ts` for utilities

### Structure Rules
| Rule | Limit |
|------|-------|
| Max component length | 200 lines |
| Max file length | 300 lines |
| Max props per component | 8 (use interface beyond this) |

### Required Patterns
```typescript
// All props must have TypeScript interfaces
interface RideCardProps {
  ride: Ride;
  onPress: (id: string) => void;
  isActive?: boolean;
}

// Use const for components
const RideCard: React.FC<RideCardProps> = ({ ride, onPress, isActive }) => {

// Use async/await (not .then chains)
const fetchRides = async () => {
  const response = await api.getRides();
};

// Use optional chaining
const name = user?.profile?.name ?? 'Unknown';
```

### Forbidden Patterns
```typescript
// ❌ No any type
const data: any = {};  // BAD
const data: RideData = {};  // GOOD

// ❌ No console.log in production
console.log("debug");  // BAD — remove before commit

// ❌ No inline styles for repeated use
style={{ padding: 10 }}  // BAD for repeated styles
styles.container  // GOOD — use StyleSheet.create()

// ❌ No hardcoded strings for API URLs
fetch("http://localhost:8000/api")  // BAD
fetch(`${API_BASE_URL}/api`)  // GOOD
```

---

## Git Conventions
- **Commit messages**: Use conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `chore:`)
- **Branch naming**: `feature/short-description`, `fix/bug-description`, `docs/what-changed`
- **No force push** to main/production branches
