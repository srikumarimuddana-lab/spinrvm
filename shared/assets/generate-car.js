const fs = require('fs');

// Solid white sedan, no transparency on body — like Uber/Waze
const svg = `<svg xmlns='http://www.w3.org/2000/svg' width='120' height='220' viewBox='0 0 120 220'>
  <defs>
    <linearGradient id='bg' x1='0' y1='0' x2='1' y2='0'>
      <stop offset='0%' stop-color='#D4D4D8'/>
      <stop offset='15%' stop-color='#E8E8EC'/>
      <stop offset='50%' stop-color='#F8F8FA'/>
      <stop offset='85%' stop-color='#E8E8EC'/>
      <stop offset='100%' stop-color='#D4D4D8'/>
    </linearGradient>
    <linearGradient id='gl' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#3B8DB5'/>
      <stop offset='100%' stop-color='#62B4D8'/>
    </linearGradient>
  </defs>

  <!-- Ground shadow -->
  <ellipse cx='60' cy='113' rx='38' ry='68' fill='#00000030'/>

  <!-- Body -->
  <rect x='22' y='8' width='76' height='204' rx='30' fill='url(#bg)'/>
  <rect x='22' y='8' width='76' height='204' rx='30' fill='none' stroke='#C0C0C4' stroke-width='1.2'/>

  <!-- Front bumper -->
  <rect x='30' y='12' width='60' height='10' rx='5' fill='#CCCCCE'/>

  <!-- Headlights -->
  <rect x='28' y='14' width='15' height='7' rx='3.5' fill='#F5D560' stroke='#D4A830' stroke-width='0.6'/>
  <rect x='77' y='14' width='15' height='7' rx='3.5' fill='#F5D560' stroke='#D4A830' stroke-width='0.6'/>

  <!-- Grille -->
  <rect x='44' y='14' width='32' height='6' rx='3' fill='#AAAAB0'/>

  <!-- Hood line -->
  <path d='M 35 42 Q 60 37 85 42' fill='none' stroke='#B8B8BC' stroke-width='1'/>

  <!-- Windshield -->
  <rect x='32' y='42' width='56' height='46' rx='13' fill='url(#gl)'/>
  <!-- Windshield glare -->
  <rect x='37' y='46' width='22' height='12' rx='6' fill='#FFFFFF50'/>

  <!-- A-pillars -->
  <line x1='32' y1='46' x2='37' y2='86' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>
  <line x1='88' y1='46' x2='83' y2='86' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>

  <!-- Roof -->
  <rect x='28' y='90' width='64' height='52' rx='8' fill='#EAEAEE'/>
  <!-- Roof center highlight -->
  <rect x='40' y='98' width='40' height='32' rx='5' fill='#F2F2F6'/>

  <!-- C-pillars -->
  <line x1='34' y1='138' x2='32' y2='150' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>
  <line x1='86' y1='138' x2='88' y2='150' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>

  <!-- Rear window -->
  <rect x='35' y='146' width='50' height='34' rx='12' fill='url(#gl)' opacity='0.85'/>

  <!-- Trunk line -->
  <path d='M 35 184 Q 60 189 85 184' fill='none' stroke='#B8B8BC' stroke-width='1'/>

  <!-- Rear bumper -->
  <rect x='30' y='198' width='60' height='10' rx='5' fill='#CCCCCE'/>

  <!-- Taillights -->
  <rect x='28' y='196' width='15' height='7' rx='3.5' fill='#E05050' stroke='#C03030' stroke-width='0.6'/>
  <rect x='77' y='196' width='15' height='7' rx='3.5' fill='#E05050' stroke='#C03030' stroke-width='0.6'/>

  <!-- License plate -->
  <rect x='46' y='200' width='28' height='5' rx='1.5' fill='#B8B8BC'/>

  <!-- Side mirrors -->
  <ellipse cx='14' cy='70' rx='8' ry='10' fill='#E0E0E4' stroke='#C0C0C4' stroke-width='0.8'/>
  <ellipse cx='14' cy='70' rx='4.5' ry='6' fill='#4A9EC0'/>
  <ellipse cx='106' cy='70' rx='8' ry='10' fill='#E0E0E4' stroke='#C0C0C4' stroke-width='0.8'/>
  <ellipse cx='106' cy='70' rx='4.5' ry='6' fill='#4A9EC0'/>

  <!-- Door lines -->
  <line x1='25' y1='88' x2='25' y2='142' stroke='#C4C4C8' stroke-width='0.8'/>
  <line x1='95' y1='88' x2='95' y2='142' stroke='#C4C4C8' stroke-width='0.8'/>

  <!-- Door handles -->
  <rect x='23' y='112' width='5' height='3' rx='1.5' fill='#B0B0B6'/>
  <rect x='92' y='112' width='5' height='3' rx='1.5' fill='#B0B0B6'/>

  <!-- Wheel wells -->
  <ellipse cx='38' cy='38' rx='10' ry='6' fill='#A0A0A6'/>
  <ellipse cx='82' cy='38' rx='10' ry='6' fill='#A0A0A6'/>
  <ellipse cx='38' cy='184' rx='10' ry='6' fill='#A0A0A6'/>
  <ellipse cx='82' cy='184' rx='10' ry='6' fill='#A0A0A6'/>
</svg>`;

const b64 = Buffer.from(svg).toString('base64');
const dataUri = 'data:image/svg+xml;base64,' + b64;

const tsContent = `// Auto-generated 3D car top-view — solid white sedan like Uber/Waze
export const CAR_TOP_IMAGE = '${dataUri}';\n`;

fs.writeFileSync('C:/Users/swarn/Documents/SpinrApp/spinr/shared/assets/carImage.ts', tsContent);
console.log('Done - solid car image generated');
