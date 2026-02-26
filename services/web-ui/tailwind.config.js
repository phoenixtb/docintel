/** @type {import('tailwindcss').Config} */
export default {
  content: ['./src/**/*.{html,js,svelte,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        primary: {
          50:  '#ecfdf5',
          100: '#d1fae5',
          200: '#a7f3d0',
          300: '#6ee7b7',
          400: '#34d399',
          500: '#10b981',
          600: '#059669',
          700: '#047857',
          800: '#065f46',
          900: '#064e3b',
          950: '#022c22',
        },
        surface: {
          DEFAULT: '#070d14',
          1: '#0d1a23',
          2: '#0f2030',
          3: '#162840',
        },
      },
      keyframes: {
        glow: {
          '0%, 100%': { boxShadow: '0 0 8px rgba(16,185,129,0.2)' },
          '50%':       { boxShadow: '0 0 20px rgba(16,185,129,0.5)' },
        },
        'shimmer-cursor': {
          '0%, 100%': { opacity: '1' },
          '50%':       { opacity: '0' },
        },
        'star-twinkle': {
          '0%, 100%': { opacity: '0.3' },
          '50%':       { opacity: '1' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to:   { opacity: '1', transform: 'translateY(0)' },
        },
      },
      animation: {
        glow:     'glow 2s ease-in-out infinite',
        cursor:   'shimmer-cursor 1s step-end infinite',
        twinkle:  'star-twinkle 3s ease-in-out infinite',
        'fade-in': 'fade-in 0.3s ease-out',
      },
      backdropBlur: {
        xs: '2px',
      },
      boxShadow: {
        'glow-sm':  '0 0 12px rgba(16,185,129,0.15)',
        'glow-md':  '0 0 24px rgba(16,185,129,0.25)',
        'glow-lg':  '0 0 48px rgba(16,185,129,0.3)',
        'glass':    '0 8px 32px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05)',
        'glass-sm': '0 4px 16px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.05)',
      },
    },
  },
  plugins: [],
};
