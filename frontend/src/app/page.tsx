'use client';
import Layout from '@/components/Layout/Layout'
import { Box, Button, Grid2, Typography } from '@mui/material';
import { useEffect, useState } from 'react';

export default function Page() {

  const [dbExists, setDBExists] = useState<boolean>(true)

  useEffect(() => {
    async function checkReady() {

      const res = await fetch(`/api/admin/setup`)
      if (!res.ok) {
        setDBExists(false)
      } else {
        // not entirely needed
        setDBExists(true)
      }
    }
    checkReady()
  }, [])

  const createDB = () => {

    
    fetch(`/api/admin/setup`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
    })
      .then(response => response.json())  
      .then(() => {
            alert("database successfully set up!")
            setDBExists(true)
      })
      .catch(error => {
        alert("Error setting up database")

        console.error('Error:', error);
      });

  };
  return (
    <Layout pageTitle={"Home Page"}>
      {!dbExists ? 
        <Box paddingTop={'2%'}>
             <Grid2
            container
            justifyContent="center"  // Centers horizontally
        >
                <Button variant="contained" color="primary" onClick={createDB}>
                    Click here to create app
                </Button>
        </Grid2>
        </Box>
      : 
      
      
      
      <Box>
        <Typography variant='h5'>Welcome to the judging app!</Typography>
        <Typography variant='body1'>Please use the menu button in the header to navigate!</Typography>

        </Box>}


    


    </Layout>

  )
}