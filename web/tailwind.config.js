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
        surface: "hsl(var(--surface))",
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
        /* 兼容旧语义 token */
        paper: "hsl(var(--canvas))",
        accent: "hsl(var(--brand))",
        border: "hsl(var(--line))",
        input: "hsl(var(--line))",
        ring: "hsl(var(--brand))",
        background: "hsl(var(--canvas))",
        foreground: "hsl(var(--ink))",
        primary: {
          DEFAULT: "hsl(var(--brand))",
          foreground: "#ffffff",
        },
        secondary: {
          DEFAULT: "hsl(var(--soft))",
          foreground: "hsl(var(--ink))",
        },
        destructive: {
          DEFAULT: "hsl(var(--danger))",
          foreground: "#ffffff",
        },
        muted: {
          DEFAULT: "hsl(var(--soft))",
          foreground: "hsl(var(--faint))",
        },
        card: {
          DEFAULT: "#ffffff",
          foreground: "hsl(var(--ink))",
        },
      },
      fontFamily: {
        sans: ['"Inter"', '"Noto Sans SC"', 'system-ui', 'sans-serif'],
        mono: ['"IBM Plex Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        card: '0 1px 2px 0 rgb(16 24 40 / 0.04), 0 1px 3px 0 rgb(16 24 40 / 0.06)',
        lift: '0 4px 8px -2px rgb(16 24 40 / 0.08), 0 2px 4px -2px rgb(16 24 40 / 0.04)',
        pop: '0 12px 24px -6px rgb(16 24 40 / 0.14), 0 4px 8px -4px rgb(16 24 40 / 0.06)',
        focus: '0 0 0 4px hsl(var(--brand) / 0.15)',
        none: '0 0 0 0 transparent',
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
