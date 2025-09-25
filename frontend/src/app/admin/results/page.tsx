'use client'
import React from "react";
import { Button, Box, Typography } from "@mui/material";
import Layout from "@/components/Layout/Layout";

export default function GetResults() {


  const downloadData = async () => {
  
    try {
        const resp = await fetch('/api/admin/results', {
            method: 'GET',
        });

        if (!resp.ok) {
            alert("Error download excel file")
            throw new Error('Error downloading the Excel file');
        }

        // Get the response as a Blob (binary data)
        const blob = await resp.blob();

        const link = document.createElement('a');
        link.href = URL.createObjectURL(blob);
        link.download = 'results.xlsx'; 

        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    } catch (error) {
        console.error('Error downloading the Excel file:', error);
    }

  };

  return (
    <Layout pageTitle="Download Results">
    <Box
      display="flex"
      justifyContent="center"
      alignItems="center"
      height="100vh"
      flexDirection="column"
    >
      <Typography variant="h4" gutterBottom>
        Export Data
      </Typography>

      <Button
        variant="contained"
        color="primary"
        onClick={downloadData}
        sx={{
          padding: "10px 20px",
          fontSize: "16px",
          marginBottom:   0, // Add margin only when button is enabled
        }}
      >
        Download
      </Button>

       
    </Box>
    </Layout>

  );
}
