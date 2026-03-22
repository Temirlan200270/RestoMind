/** Сборка CSS для админки (вместо cdn.tailwindcss.com). */
module.exports = {
  content: ['./app/templates/**/*.html'],
  theme: {
    extend: {
      screens: {
        xs: '480px',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
      colors: {
        brand: {
          50: '#f0f7ff',
          100: '#e0effe',
          200: '#bae0fd',
          400: '#5bb8f9',
          500: '#3b9ef5',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1a2e5e',
        },
      },
    },
  },
  plugins: [],
};
