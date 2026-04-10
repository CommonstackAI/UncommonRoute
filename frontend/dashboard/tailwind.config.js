/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Doto"', '"Space Mono"', 'monospace'],
        sans: ['"Space Grotesk"', '"DM Sans"', 'system-ui', 'sans-serif'],
        mono: ['"Space Mono"', '"JetBrains Mono"', 'monospace'],
      },
      colors: {
        n: {
          black: '#000000',
          surface: '#111111',
          raised: '#1A1A1A',
          border: '#222222',
          'border-vis': '#333333',
          disabled: '#666666',
          secondary: '#999999',
          primary: '#E8E8E8',
          display: '#FFFFFF',
          accent: '#D71921',
          success: '#4A9E5C',
          warning: '#D4A843',
          interactive: '#5B9BF6',
        },
      },
      spacing: {
        '2xs': '2px',
        'xs': '4px',
        'sm': '8px',
        'md': '16px',
        'lg': '24px',
        'xl': '32px',
        '2xl': '48px',
        '3xl': '64px',
        '4xl': '96px',
      },
      borderRadius: {
        'technical': '4px',
        'compact': '8px',
        'card': '12px',
        'pill': '999px',
      },
    },
  },
  plugins: [],
};
