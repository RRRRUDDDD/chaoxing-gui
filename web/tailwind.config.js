/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        /* 现代工具风 · 石墨蓝灰 */
        ink: "hsl(var(--ink))",
        body: "hsl(var(--body))",
        faint: "hsl(var(--faint))",
        canvas: "hsl(var(--canvas))",
        soft: "hsl(var(--soft))",
        line: "hsl(var(--line))",
        brand: {
          DEFAULT: "hsl(var(--brand))",
          dark: "hsl(var(--brand-dark))",
          soft: "hsl(var(--brand-soft))",
        },
        success: { DEFAULT: "hsl(var(--success))", ink: "hsl(var(--success-ink))" },
        danger: "hsl(var(--danger))",
        warning: { DEFAULT: "hsl(var(--warning))", ink: "hsl(var(--warning-ink))" },
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(16 24 40 / 0.04), 0 1px 3px 0 rgb(16 24 40 / 0.06)',
        lift: '0 4px 8px -2px rgb(16 24 40 / 0.08), 0 2px 4px -2px rgb(16 24 40 / 0.04)',
      },
      spacing: {
        '4.5': '1.125rem',
      },
      borderRadius: {
        xl: '12px',
        '2xl': '16px',
      },
      keyframes: {
        'stagger-up': {
          from: { opacity: '0', transform: 'translateY(12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'pop-in': {
          from: { opacity: '0', transform: 'scale(0.98)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
      },
      animation: {
        'stagger-up': 'stagger-up 0.5s cubic-bezier(0.22, 1, 0.36, 1) both',
        'fade-in': 'fade-in 0.25s ease-out both',
        'pop-in': 'pop-in 0.3s cubic-bezier(0.22, 1, 0.36, 1) both',
      },
    },
  },
  plugins: [],
}
