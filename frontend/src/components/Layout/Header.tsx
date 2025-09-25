'use client';
import { AppBar, Box, IconButton, Menu, Toolbar, Typography } from '@mui/material';
import { Menu as MenuIcon } from '@mui/icons-material';
import { useState } from 'react';
import LinkMenuItem from '@/components/Layout/LinkMenuItem';

type Props = {
  pageTitle: string;
};

export default function Header({ pageTitle }: Props) {
  const [anchorEl, setAnchorEl] = useState<null | HTMLElement>(null);

  const handleClick = (event: React.MouseEvent<HTMLElement>) => {
    setAnchorEl(event.currentTarget);
  };

  const handleClose = () => {
    setAnchorEl(null);
  };

  return (
    <Box sx={{ flexGrow: 1 }} component={'header'}>
      <AppBar position='static'>
        <Toolbar>
          <IconButton
            size='large'
            edge='start'
            color='inherit'
            aria-label='menu'
            sx={{ mr: 2 }}
            onClick={handleClick}
          >
            <MenuIcon />
          </IconButton>

          <Typography variant='h6' component='div' sx={{ flexGrow: 1 }}>
             {pageTitle}
          </Typography>

          {/* <Button color='inherit'>Login</Button> */}
        </Toolbar>
      </AppBar>

      <Menu
        anchorEl={anchorEl}
        open={Boolean(anchorEl)}
        onClose={handleClose}
        anchorOrigin={{
          vertical: 'bottom',  
          horizontal: 'left',  
        }}
        transformOrigin={{
          vertical: 'top',  
          horizontal: 'left',  
        }}
      >
        <LinkMenuItem path='/admin' name='Admin Home' handleClose={handleClose} />
        <LinkMenuItem path='/admin/results' name='Admin Results' handleClose={handleClose} />
        <LinkMenuItem path='/judges' name='Judges Home' handleClose={handleClose} />
      </Menu>
    </Box>
  );
}
