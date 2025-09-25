import type { Metadata } from 'next';
import { AppRouterCacheProvider } from '@mui/material-nextjs/v15-appRouter';
import { ThemeProvider } from '@mui/material/styles';
import theme from '@/styles/theme'
import { CssBaseline } from '@mui/material';


export const metadata: Metadata = {
  title: 'Assemblage',
  description: 'Control for the Assemblage Project',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang='en'>
    <body>
        <AppRouterCacheProvider>
        <CssBaseline />

        <ThemeProvider theme={theme}>
        {children}
        </ThemeProvider>
       </AppRouterCacheProvider>
    </body>
  </html>
  );
}
