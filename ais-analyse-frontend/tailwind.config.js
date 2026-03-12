/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{vue,js,ts,jsx,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        navy: {
          900: '#0a0f1e',
          800: '#111827',
          700: '#1a2332',
          600: '#243044',
          500: '#2d3c56',
        },
        ocean: {
          600: '#0284C7',
          500: '#0EA5E9',
          400: '#38BDF8',
          300: '#7DD3FC',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
    },
  },
  plugins: [],
}
