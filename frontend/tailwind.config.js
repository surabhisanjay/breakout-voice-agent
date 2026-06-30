/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        background: '#0a0b0d',
        card: '#12141a',
        primary: '#3b82f6',
        accent: '#10b981',
        border: '#1f2937'
      }
    },
  },
  plugins: [],
}
