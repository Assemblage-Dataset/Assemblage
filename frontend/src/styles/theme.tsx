'use client';
import { createTheme } from '@mui/material/styles';

const theme = createTheme({
  palette: {
    primary: {
      main: '#F76900', // Syracuse Orange
    },
    secondary: {
      main: '#000E54', // Blue accents
    },
    background: {
      default: '#F1F1F1', // Light gray background
      paper: '#FFFFFF', // White paper for card components
    },
    text: {
      primary: '#212121', // Dark text for high contrast
      secondary: '#757575', // Light gray text for secondary content
    },
  },
  typography: {
    fontFamily: '"Roboto", sans-serif', // Clean sans-serif font
    h1: {
      fontSize: '2rem',
      fontWeight: 'bold',
    },
    h2: {
      fontSize: '1.75rem',
      fontWeight: 'bold',
    },
    body1: {
      fontSize: '1rem',
    },
  },
  components: {
    MuiButton: {
      styleOverrides: {
        root: {
          textTransform: 'none',
          padding: '10px 20px',
        },
      },
    },
    MuiAppBar: {
      styleOverrides: {
        root: {
          backgroundColor: "primary.main", // Blue for the app bar
        },
      },
    },
  },
});

export default theme;
