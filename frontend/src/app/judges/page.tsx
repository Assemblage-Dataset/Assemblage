'use client';
import Layout from '@/components/Layout/Layout'
import { Box, IconButton, Paper, Table, TableBody, TableCell, TableContainer, TableHead, TableRow } from '@mui/material';
import { useEffect, useState } from 'react';
import { judge } from '../../types/judge';
import Link from 'next/link';
import { ArrowForward } from '@mui/icons-material';

export default function Judges() {

  const [judgesData, setJudgesData] = useState<judge[]>([])

  useEffect(() => {
    async function fetchPosts() {
      const res = await fetch(`/api/judges`)


      if (!res.ok) {
        alert("Fetching judges")
        throw new Error('Error downloading the Excel file');
      } else {

        const data: judge[] = await res.json()
        setJudgesData(data)
      }



    }
    fetchPosts()
  }, [])


  return (
    <Layout pageTitle={'Judges'}>
      <Box paddingTop={'2%'}>
        <TableContainer component={Paper}>
          <Table aria-label="judges table">
            <TableHead>
              <TableRow>
                <TableCell>ID</TableCell>
                <TableCell>First Name</TableCell>
                <TableCell>Last Name</TableCell>
                <TableCell>Posters</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {judgesData.map((judge) => (
                <TableRow key={judge.id}>
                  <TableCell>{judge.id}</TableCell>
                  <TableCell>{judge.first_name}</TableCell>
                  <TableCell>{judge.last_name}</TableCell>
                  <TableCell>
                    <Link href={`/judges/${judge.id}`} passHref>
                      <IconButton aria-label="view posters">
                        <ArrowForward />
                      </IconButton>
                    </Link>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>

      </Box>



    </Layout>

  )
}